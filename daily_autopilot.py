import argparse
import ctypes
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime

if sys.platform == "win32":
    # Prevent any child process (e.g. Playwright node.exe / Chrome helpers) from flashing a console window
    _orig_popen = subprocess.Popen

    class _SilentPopen(_orig_popen):
        def __init__(self, *args, **kwargs):
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            super().__init__(*args, **kwargs)

    subprocess.Popen = _SilentPopen

    try:
        if sys.stdout is not None:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr is not None:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRAPER_DIR = os.path.join(ROOT_DIR, "scraper")
OUTREACH_DIR = os.path.join(ROOT_DIR, "outreach")
LOGS_DIR = os.path.join(ROOT_DIR, "logs")
LOCK_FILE = os.path.join(LOGS_DIR, "autopilot.lock")
CURSOR_FILE = os.path.join(SCRAPER_DIR, "scrape_cursor.json")

for p in (ROOT_DIR, SCRAPER_DIR, OUTREACH_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)


class _TeeLogger:
    """Write stdout/stderr simultaneously to console and daily log file."""

    def __init__(self, log_path: str):
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        self.terminal = sys.stdout
        self.log = open(log_path, "a", encoding="utf-8", errors="replace")

    def write(self, message):
        try:
            if self.terminal:
                self.terminal.write(message)
        except Exception:
            pass
        try:
            self.log.write(message)
        except Exception:
            pass

    def flush(self):
        try:
            if self.terminal:
                self.terminal.flush()
        except Exception:
            pass
        try:
            self.log.flush()
        except Exception:
            pass

    def close(self):
        try:
            self.log.close()
        except Exception:
            pass


def _is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        exit_code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return exit_code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_lock() -> bool:
    """Ensure only ONE autopilot run executes at a time; auto-clean stale PID locks."""
    os.makedirs(LOGS_DIR, exist_ok=True)
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r", encoding="utf-8") as f:
                old_pid = int(f.read().strip() or "0")
            if _is_pid_running(old_pid):
                print(f"⏸️ Autopilot already running (PID {old_pid}). Exiting cleanly to avoid overlap.")
                return False
            print(f"🧹 Clearing stale lockfile from exited PID {old_pid}.")
        except Exception:
            pass

    with open(LOCK_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    return True


def release_lock():
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception:
        pass


def wait_for_internet(max_wait_sec: int = 300) -> bool:
    """Wait gracefully for internet connectivity (handles PC wake/boot Wi-Fi reconnect delays)."""
    deadline = time.time() + max_wait_sec
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        for host, port in (("dns.google", 443), ("smtp.gmail.com", 465)):
            try:
                with socket.create_connection((host, port), timeout=5):
                    if attempt > 1:
                        print(f"🌐 Internet connected after {attempt} checks.", flush=True)
                    return True
            except OSError:
                continue
        if attempt == 1:
            print("⏳ Waiting for internet connection (PC may have just booted/woken up)...", flush=True)
        time.sleep(10)
    print("❌ No internet connection after 5 minutes. Exiting cleanly; Task Scheduler will retry later.", flush=True)
    return False


def _load_cursor() -> int:
    if os.path.exists(CURSOR_FILE):
        try:
            with open(CURSOR_FILE, "r", encoding="utf-8") as f:
                return int(json.load(f).get("next_index", 0))
        except Exception:
            return 0
    return 0


def _save_cursor(next_idx: int, last_pair: str):
    with open(CURSOR_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "next_index": next_idx,
                "last_pair": last_pair,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            f,
            indent=2,
        )


def auto_scrape_tier1_refill(pairs_to_scrape: int = 2, max_per_pair: int = 18):
    """Rotate cleanly through Tier-1 (niche, city) pairs headlessly when ready lead pool runs low."""
    import importlib.util

    # Load scraper/config.py explicitly so it never collides with outreach/config.py
    spec = importlib.util.spec_from_file_location("scraper_cfg", os.path.join(SCRAPER_DIR, "config.py"))
    scraper_cfg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scraper_cfg)

    queries = list(getattr(scraper_cfg, "SEARCH_QUERIES", []))
    cities = list(getattr(scraper_cfg, "TARGET_CITIES", []))
    if not queries or not cities:
        print("⚠️ No Tier-1 queries/cities found in scraper/config.py.")
        return

    # Interleave cities across queries so every day hits varied cities & high-LTV niches
    all_pairs = [(q, c) for c in cities for q in queries]
    cursor = _load_cursor() % len(all_pairs)

    # Import local_scraper safely with scraper/ first in sys.path
    orig_path = list(sys.path)
    sys.path.insert(0, SCRAPER_DIR)
    old_config = sys.modules.pop("config", None)
    try:
        import local_scraper
        local_scraper.HEADLESS = True  # NEVER pop up Chrome windows during scheduled autopilot!

        for step in range(pairs_to_scrape):
            q, c = all_pairs[(cursor + step) % len(all_pairs)]
            print(f"\n🔍 [Tier-1 Auto-Refill {step + 1}/{pairs_to_scrape}] '{q}' in '{c}' (headless)...", flush=True)
            try:
                found = local_scraper.scrape_leads(q, c, max_results=max_per_pair)
                with_em = sum(1 for x in found if x.get("email"))
                print(f"   ✅ Collected {len(found)} leads ({with_em} with email) for '{q}' in '{c}'", flush=True)
            except Exception as err:
                print(f"   ⚠️ Scrape error on '{q}' in '{c}': {err}", flush=True)
            _save_cursor((cursor + step + 1) % len(all_pairs), f"{q} in {c}")
    finally:
        if old_config is not None:
            sys.modules["config"] = old_config
        sys.path[:] = orig_path


def run_daily_autopilot(dry_run: bool = False, force_scrape: bool = False):
    today_str = datetime.now().strftime("%Y-%m-%d")
    log_path = os.path.join(LOGS_DIR, f"daily_{today_str}.log")
    tee = _TeeLogger(log_path)
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = tee
    sys.stderr = tee

    print("\n" + "=" * 70)
    print(f"🤖 LEADPIPELINE DAILY AUTOPILOT STARTED — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   Mode: {'DRY-RUN (no scraping/sending)' if dry_run else 'LIVE AUTOPILOT'}")
    print("=" * 70)

    if not acquire_lock():
        sys.stdout, sys.stderr = old_stdout, old_stderr
        tee.close()
        return

    try:
        if not wait_for_internet(max_wait_sec=30 if dry_run else 300):
            return

        # Ensure outreach/config.py is loaded as `config`
        sys.path.insert(0, OUTREACH_DIR)
        import config as outreach_cfg
        min_ready_pool = getattr(outreach_cfg, "MIN_READY_POOL", 35)
        daily_limit = getattr(outreach_cfg, "DAILY_SEND_LIMIT", 30)

        # STEP 1: Check Gmail IMAP for hot replies & notify ALERT_EMAIL immediately
        print("\n--- STEP 1/4: Hot-Reply Watcher & IMAP Sync ---", flush=True)
        from outreach.reply_notifier import check_and_notify_replies
        check_and_notify_replies()

        # STEP 2: Check ready-to-send Tier-1 pool; auto-scrape headlessly if below MIN_READY_POOL
        print("\n--- STEP 2/4: Tier-1 Lead Pool Check & Auto-Refill ---", flush=True)
        from outreach.pitch_sender import load_qualified_leads, load_state, build_send_queue, count_sent_today
        state, legacy_pitched = load_state()
        sent_today = count_sent_today(state)
        qualified = load_qualified_leads() if os.path.exists(os.path.join(ROOT_DIR, "qualified_leads.json")) else []
        ready_queue = build_send_queue(qualified, state, legacy_pitched)
        print(f"📊 Ready queue: {len(ready_queue)} leads | Sent today: {sent_today}/{daily_limit} | Pool target: {min_ready_pool}", flush=True)

        if force_scrape or len(ready_queue) < min_ready_pool:
            if dry_run:
                print(f"👀 [DRY-RUN] Would auto-scrape 2 fresh Tier-1 (niche, city) pairs (cursor={_load_cursor()}).", flush=True)
            else:
                auto_scrape_tier1_refill(pairs_to_scrape=2, max_per_pair=18)
        else:
            print("✅ Tier-1 pool healthy — no scraping needed today.", flush=True)

        # STEP 3: Audit newest uncontacted Tier-1 leads with Gemini + build gemini-4-hydrogen Whale demos
        print("\n--- STEP 3/4: Gemini Audit + 4-Hydrogen Whale Redesigns ---", flush=True)
        if dry_run:
            print("👀 [DRY-RUN] Would audit & score up to 35 newest uncontacted Tier-1 leads.", flush=True)
        else:
            from analyzer.gemini_engine import run_audit_and_qualification
            run_audit_and_qualification(limit=35)

        # STEP 4: Send up to DAILY_SEND_LIMIT (30/day) with Direct Emails first & Day-3/7 threads
        print("\n--- STEP 4/4: Gmail-Primary-Safe Threaded Outreach (30/day Cap) ---", flush=True)
        from outreach.pitch_sender import run_outreach
        run_outreach(preview=dry_run, review=False, skip_imap=True)

        print(f"\n✅ AUTOPILOT FINISHED CLEANLY — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"📝 Full log saved: {log_path}")

    except KeyboardInterrupt:
        print("\n⚠️ Autopilot stopped manually (Ctrl+C). All completed work is safely saved.")
    except Exception as e:
        print(f"\n❌ Unexpected autopilot error: {e}", flush=True)
        import traceback
        traceback.print_exc()
    finally:
        release_lock()
        sys.stdout, sys.stderr = old_stdout, old_stderr
        tee.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bulletproof Daily Autopilot for LeadPipeline")
    parser.add_argument("--dry-run", action="store_true", help="Test full autopilot flow without scraping or sending")
    parser.add_argument("--force-scrape", action="store_true", help="Force scraping 2 fresh Tier-1 pairs even if pool is full")
    args = parser.parse_args()
    run_daily_autopilot(dry_run=args.dry_run, force_scrape=args.force_scrape)

#!/usr/bin/env python3
"""
yt_reaper.py - YouTube Channel Reaper (v3)
===========================================
Single-file Telegram bot + HTTP API + browser controller.

All secrets from environment variables ONLY.
No hardcoded tokens, no hardcoded secrets.

Usage:
  export BOT_TOKEN="your_telegram_bot_token"
  export API_SECRET="choose_a_password"
  python yt_reaper.py
"""

import os, re, sys, json, time, random, logging, hashlib, asyncio, threading, traceback, signal, queue
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any, Callable
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

# --- Telegram ---
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters, ContextTypes

# --- Selenium ---
try:
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException, WebDriverException
    SELENIUM_OK = True
except ImportError:
    SELENIUM_OK = False


# ============================================================
# CONFIG (env vars ONLY)
# ============================================================
BOT_TOKEN = "8760415886:AAH-JhrbqKGtfyc_-zJ4ewGedle2Q-vvJj0"
if not BOT_TOKEN:
    print("=" * 60)
    print("FATAL: BOT_TOKEN environment variable is required.")
    print()
    print("  You leaked token 8760415886:AAH-JhrbqKGtfyc_-zJ4ewGedle2Q-vvJj0")
    print("  in the original script. It has been REMOVED from this version.")
    print("  Go to @BotFather on Telegram and revoke that token NOW.")
    print()
    print("  Then set a NEW token:")
    print("    export BOT_TOKEN='your_new_token_here'")
    print("=" * 60)
    sys.exit(1)

API_SECRET = "ReaperSecurePass2026"
if not API_SECRET:
    print("FATAL: API_SECRET environment variable is required.")
    print("Usage: export API_SECRET='your_password_here'")
    sys.exit(1)

# Configuration with sensible defaults
API_PORT   = int(os.environ.get("API_PORT", "7777"))
PROFILE_DIR = os.environ.get("PROFILE_DIR", "./chrome_profile")
MAX_VIDEOS = int(os.environ.get("MAX_VIDEOS", "200"))
DELAY_MIN  = float(os.environ.get("DELAY_MIN", "8"))
DELAY_MAX  = float(os.environ.get("DELAY_MAX", "15"))
TIMEOUT_SHORT = int(os.environ.get("TIMEOUT_SHORT", "8"))
TIMEOUT_LONG  = int(os.environ.get("TIMEOUT_LONG", "15"))
PAGE_LOAD_TIMEOUT = int(os.environ.get("PAGE_LOAD_TIMEOUT", "60"))
JOB_TTL_HOURS = int(os.environ.get("JOB_TTL_HOURS", "24"))
WORKER_COUNT = int(os.environ.get("WORKER_COUNT", "1"))

# Conversation states
MENU, AWAIT_TARGET, AWAIT_CONFIRM = range(3)

# --- Logging ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
log = logging.getLogger("reaper")


# ============================================================
# VIDEO ID EXTRACTOR
# ============================================================
def extract_video_id(text: str) -> Optional[str]:
    if not text: return None
    s = text.strip()
    if re.match(r'^[a-zA-Z0-9_-]{11}$', s): return s
    m = re.search(r'youtu\.be/([a-zA-Z0-9_-]{11})', s)
    if m: return m.group(1)
    m = re.search(r'youtube\.com/(?:watch\?v=|embed/|v/|shorts/)([a-zA-Z0-9_-]{11})', s)
    if m: return m.group(1)
    parsed = urlparse(s)
    if 'youtube.com' in parsed.netloc:
        qs = parse_qs(parsed.query)
        vid = qs.get('v', [None])[0]
        if vid and len(vid) == 11: return vid
    return None


# ============================================================
# JOB QUEUE SYSTEM
# ============================================================
class Job:
    """Represents a unit of work for the browser worker."""
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    def __init__(self, job_type: str, payload: dict):
        self.id = hashlib.md5(f"{time.time_ns()}{random.random()}".encode()).hexdigest()[:12]
        self.type = job_type
        self.payload = payload
        self.status = self.PENDING
        self.result = None
        self.error = None
        self.created_at = time.time()
        self.completed_at: Optional[float] = None
        self.progress_callback: Optional[Callable] = None
        self._event = threading.Event()

    def wait(self, timeout=None) -> Any:
        self._event.wait(timeout=timeout)
        return self.result

    def set_result(self, result):
        self.result = result
        self.status = self.DONE
        self.completed_at = time.time()
        self._event.set()

    def set_error(self, error):
        self.error = error
        self.status = self.FAILED
        self.completed_at = time.time()
        self._event.set()

    def cancel(self):
        if self.status in (self.PENDING, self.RUNNING):
            self.status = self.CANCELLED
            self.completed_at = time.time()
            self._event.set()

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at


class JobQueue:
    """Thread-safe job queue with automatic eviction of old jobs."""

    def __init__(self, worker_count=1, ttl_hours=24):
        self._queue: queue.Queue[Job] = queue.Queue()
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._worker_count = worker_count
        self._ttl_seconds = ttl_hours * 3600
        self._workers = []
        self._stop_event = threading.Event()
        # Store separate profiles for multi-worker setups
        self._profile_index = 0
        self._profile_lock = threading.Lock()

    def get_next_profile(self) -> str:
        """Round-robin profile assignment for multiple workers."""
        with self._profile_lock:
            idx = self._profile_index
            self._profile_index += 1
        if self._worker_count <= 1:
            return PROFILE_DIR
        return f"{PROFILE_DIR}_worker{idx}"

    def start(self, worker_fn: Callable[[Job], None]):
        self._stop_event.clear()
        for i in range(self._worker_count):
            t = threading.Thread(
                target=self._worker_loop,
                args=(worker_fn,),
                daemon=True,
                name=f"reaper-worker-{i}"
            )
            t.start()
            self._workers.append(t)
        # Start background eviction thread
        evict = threading.Thread(target=self._eviction_loop, daemon=True, name="job-evictor")
        evict.start()

    def stop(self):
        self._stop_event.set()
        for _ in self._workers:
            self._queue.put(None)

    def _worker_loop(self, worker_fn: Callable[[Job], None]):
        while not self._stop_event.is_set():
            try:
                job = self._queue.get(timeout=1)
            except queue.Empty:
                continue
            if job is None:
                break
            try:
                with self._lock:
                    job.status = Job.RUNNING
                worker_fn(job)
            except Exception as e:
                log.error(f"Worker error on job {job.id}: {traceback.format_exc()}")
                job.set_error(str(e))
            finally:
                self._queue.task_done()

    def _eviction_loop(self):
        """Periodically purge old completed jobs to prevent memory leaks."""
        while not self._stop_event.is_set():
            time.sleep(300)  # every 5 minutes
            self._evict_old_jobs()

    def _evict_old_jobs(self):
        now = time.time()
        to_delete = []
        with self._lock:
            for jid, job in self._jobs.items():
                if job.completed_at and (now - job.completed_at) > self._ttl_seconds:
                    to_delete.append(jid)
            for jid in to_delete:
                del self._jobs[jid]
        if to_delete:
            log.debug(f"Evicted {len(to_delete)} old jobs from memory")

    def submit(self, job: Job) -> str:
        job.status = Job.PENDING
        with self._lock:
            self._jobs[job.id] = job
        self._queue.put(job)
        return job.id

    def get_job(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def get_status(self, job_id: str) -> Optional[Dict]:
        job = self.get_job(job_id)
        if not job:
            return None
        return {
            "id": job.id,
            "type": job.type,
            "status": job.status,
            "created_at": job.created_at,
            "completed_at": job.completed_at,
            "has_result": job.result is not None,
            "age_seconds": job.age_seconds,
        }

    def list_jobs(self, limit=50) -> List[Dict]:
        with self._lock:
            sorted_jobs = sorted(self._jobs.values(), key=lambda x: x.created_at, reverse=True)
            return [
                {
                    "id": j.id,
                    "type": j.type,
                    "status": j.status,
                    "created_at": j.created_at,
                    "completed_at": j.completed_at,
                    "age_seconds": j.age_seconds,
                }
                for j in sorted_jobs[:limit]
            ]

    def cancel_job(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        if job and job.status in (Job.PENDING, Job.RUNNING):
            job.cancel()
            return True
        return False

    def active_job_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j.status == Job.RUNNING)

    def pending_job_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j.status == Job.PENDING)

    def is_busy(self) -> bool:
        return self.active_job_count() >= self._worker_count


# ============================================================
# BROWSER CONTROLLER
# ============================================================
class Reaper:
    """Browser automation instance. One per worker."""

    def __init__(self, profile_dir: str, worker_id: int = 0):
        self.driver = None
        self._stop = False
        self.profile = Path(profile_dir).resolve()
        self.profile.mkdir(parents=True, exist_ok=True)
        self._init_lock = threading.Lock()
        self._worker_id = worker_id

        # Check for stale lock files
        lock_file = self.profile / "SingletonLock"
        if lock_file.exists():
            log.warning(f"Stale lock file detected: {lock_file}")
            log.warning("If Chrome crashed, delete this file manually.")

    def _ensure(self):
        if self.driver is not None:
            try:
                _ = self.driver.title
                return
            except Exception:
                self._cleanup_driver()
        with self._init_lock:
            if self.driver is not None:
                return
            log.info(f"[Worker {self._worker_id}] Starting browser with profile: {self.profile}")
            opts = uc.ChromeOptions()
            opts.add_argument("--window-size=1366,768")
            opts.add_argument("--disable-blink-features=AutomationControlled")
            opts.add_argument("--no-first-run")
            opts.add_argument("--no-default-browser-check")
            opts.add_argument(f"--user-data-dir={self.profile}")
            opts.add_argument("--lang=en")
            opts.add_experimental_option("excludeSwitches", ["enable-automation"])
            opts.add_experimental_option("useAutomationExtension", False)
            self.driver = uc.Chrome(options=opts, use_subprocess=True)
            self.driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
            log.info(f"[Worker {self._worker_id}] Browser ready")

    def _cleanup_driver(self):
        try:
            if self.driver:
                self.driver.quit()
        except:
            pass
        self.driver = None

    def die(self):
        self._stop = True
        self._cleanup_driver()

    # --- UI helpers ---
    def _pause(self, a=0.5, b=2.0): time.sleep(random.uniform(a, b))

    def _more(self) -> bool:
        selectors = [
            "button[aria-label*='More actions'], button[aria-label*='More']",
            "ytd-menu-renderer yt-icon-button#button button",
            "ytd-watch-metadata yt-icon-button#button button",
        ]
        for sel in selectors:
            try:
                self._pause()
                WebDriverWait(self.driver, TIMEOUT_SHORT).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
                ).click()
                return True
            except:
                continue
        return False

    def _report_btn(self) -> bool:
        """Locale-agnostic report button detection using XPath translate()."""
        try:
            xpaths = [
                "//*[self::a or self::button or self::tp-yt-paper-item or self::yt-formatted-string][contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'report')]",
                "//ytd-menu-navigation-item-renderer//*[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'report')]",
            ]
            for xp in xpaths:
                try:
                    WebDriverWait(self.driver, TIMEOUT_SHORT).until(
                        EC.element_to_be_clickable((By.XPATH, xp))
                    ).click()
                    self._pause()
                    return True
                except:
                    continue
        except:
            pass
        # Fallback
        try:
            items = self.driver.find_elements(
                By.CSS_SELECTOR,
                "ytd-menu-popup-renderer tp-yt-paper-item, ytd-menu-navigation-item-renderer a"
            )
            if items:
                items[-1].click()
                self._pause()
                return True
        except:
            pass
        return False

    def _reason(self) -> bool:
        try:
            rg = WebDriverWait(self.driver, TIMEOUT_SHORT).until(
                EC.presence_of_element_located((By.TAG_NAME, "tp-yt-paper-radio-group"))
            )
            btns = rg.find_elements(By.TAG_NAME, "tp-yt-paper-radio-button")
        except:
            return False
        if not btns:
            return False
        kw = ["spam", "harass", "bully", "misleading", "hate", "abuse"]
        best, sc = None, -1
        for i, b in enumerate(btns):
            try:
                label = b.text.lower()
                for k in kw:
                    if k in label and len(k) > sc:
                        sc, best = len(k), i
            except:
                continue
        idx = best if best is not None else min(3, len(btns)-1)
        try:
            self._pause()
            btns[idx].click()
            return True
        except:
            return False

    def _secondary(self):
        try:
            dd = self.driver.find_elements(By.CSS_SELECTOR, "tp-yt-paper-dropdown-menu")
            if dd:
                self._pause()
                dd[0].click()
                time.sleep(0.5)
            items = self.driver.find_elements(By.CSS_SELECTOR, "tp-yt-paper-listbox tp-yt-paper-item")
            if items:
                items[0].click()
        except:
            pass

    def _next(self) -> bool:
        try:
            WebDriverWait(self.driver, TIMEOUT_SHORT).until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//button[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'next')]"
                ))
            ).click()
            self._pause()
            return True
        except:
            return False

    def _comments(self, text: str):
        """Type comment text. Uses JS paste for speed, then random delay for natural feel."""
        if not text:
            return
        text = text[:500]  # cap at 500 chars
        try:
            ta = self.driver.find_element(By.CSS_SELECTOR, "textarea, tp-yt-iron-autogrow-textarea textarea")
            self._pause()
            ta.click()
            ta.clear()
            # Use JavaScript to set value directly (much faster than char-by-char)
            self.driver.execute_script(
                "arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('input', {bubbles: true}));",
                ta, text
            )
            time.sleep(random.uniform(0.3, 0.8))  # brief pause to simulate human
        except:
            pass

    def _submit(self) -> bool:
        try:
            WebDriverWait(self.driver, TIMEOUT_SHORT).until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//button[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'report') or contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'submit') or contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'send')]"
                ))
            ).click()
            return True
        except:
            return False

    def _close(self):
        try:
            WebDriverWait(self.driver, TIMEOUT_SHORT).until(
                EC.element_to_be_clickable((
                    By.CSS_SELECTOR,
                    "yt-fancy-dismissible-dialog-renderer button, yt-button-renderer button"
                ))
            ).click()
        except:
            pass

    # --- Operations ---
    def process_job(self, job: Job):
        self._current_job = job
        try:
            if job.type == "scrape":
                self._handle_scrape(job)
            elif job.type == "batch_report":
                self._handle_batch_report(job)
            else:
                job.set_error(f"Unknown job type: {job.type}")
        except Exception as e:
            log.error(f"[Worker {self._worker_id}] Job {job.id} failed: {traceback.format_exc()}")
            job.set_error(str(e))
        finally:
            self._current_job = None

    def _handle_scrape(self, job: Job):
        url = job.payload.get("url", "")
        if not url:
            job.set_error("No URL provided")
            return
        self._ensure()
        if "/videos" not in url:
            url = url.rstrip("/") + "/videos"
        self.driver.get(url)
        time.sleep(5)
        try:
            cookie_btn = self.driver.find_element(By.CSS_SELECTOR, "form button, [aria-label*='Accept']")
            cookie_btn.click()
            time.sleep(2)
        except:
            pass

        ids, seen = set(), set()
        last = self.driver.execute_script("return document.documentElement.scrollHeight")
        stale = 0
        max_videos = job.payload.get("max_videos", MAX_VIDEOS)
        # Adaptive stale ceiling: at least 10, at most max_videos // 2
        stale_threshold = max(10, max_videos // 2)

        while len(ids) < max_videos and stale < stale_threshold:
            if job.status == Job.CANCELLED:
                job.set_result({"videos": list(ids), "count": len(ids), "cancelled": True})
                return
            self.driver.execute_script("window.scrollTo(0, document.documentElement.scrollHeight);")
            time.sleep(random.uniform(2.5, 4))
            for link in self.driver.find_elements(By.CSS_SELECTOR, "a#video-title-link, a#thumbnail"):
                h = link.get_attribute("href")
                if h and h not in seen:
                    seen.add(h)
                    v = extract_video_id(h)
                    if v:
                        ids.add(v)
            nh = self.driver.execute_script("return document.documentElement.scrollHeight")
            if nh == last:
                stale += 1
            else:
                stale = 0
            last = nh

        result_vids = list(ids)[:max_videos]
        job.set_result({
            "videos": result_vids,
            "count": len(result_vids),
            "total_found": len(ids),
            "stopped_early": len(ids) > max_videos,
        })

    def _handle_batch_report(self, job: Job):
        vids = job.payload.get("videos", [])
        comments = job.payload.get("comments", "")
        if not vids:
            job.set_error("No videos provided")
            return

        total = len(vids)
        successful, failed = [], []
        for i, vid in enumerate(vids):
            if job.status == Job.CANCELLED:
                break
            ok, msg = self._report_one(vid, comments if i == 0 else "")
            if ok:
                successful.append(vid)
            else:
                failed.append([vid, msg])
            log.info(f"  [{i+1}/{total}] {vid}: {'OK' if ok else msg}")
            if job.progress_callback:
                try:
                    job.progress_callback(i+1, total, vid, ok, msg)
                except:
                    pass
            if i < total - 1 and job.status != Job.CANCELLED:
                delay = random.uniform(DELAY_MIN, DELAY_MAX)
                # Adaptive backoff on failures
                if failed and len(failed) >= len(successful):
                    delay = min(delay * 1.5, 30)  # cap at 30s
                time.sleep(delay)

        job.set_result({
            "total": total,
            "attempted": i + 1,
            "successful": successful,
            "failed": failed,
        })

    def _report_one(self, vid: str, comments: str = "") -> Tuple[bool, str]:
        try:
            self._ensure()
            self.driver.get(f"https://youtu.be/{vid}")
            time.sleep(random.uniform(3, 5))
            if not self._more():
                return False, "more_btn"
            self._pause(1, 1.5)
            if not self._report_btn():
                return False, "report_btn"
            self._pause(1, 2)
            if not self._reason():
                return False, "reason"
            self._pause(1, 1.5)
            self._secondary()
            self._pause(0.5, 1)
            self._next()
            self._pause(0.5, 1.5)
            if comments:
                self._comments(comments)
                self._pause(0.5, 1)
            if not self._submit():
                return False, "submit_btn"
            time.sleep(random.uniform(2, 3))
            self._close()
            return True, "ok"
        except WebDriverException as e:
            return False, f"browser:{e}"
        except Exception as e:
            return False, f"err:{e}"

    def check_login(self) -> bool:
        try:
            self._ensure()
            self.driver.get("https://www.youtube.com")
            time.sleep(3)
            self.driver.find_element(By.CSS_SELECTOR, "button#avatar-btn")
            return True
        except:
            return False

    def get_ip(self) -> str:
        try:
            self._ensure()
            self.driver.get("https://api.ipify.org?format=json")
            time.sleep(2)
            return json.loads(self.driver.find_element(By.TAG_NAME, "pre").text).get("ip", "?")
        except:
            return "?"


# ============================================================
# HTTP API
# ============================================================

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class APIHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/ping":
            q = self.server.job_queue
            ip = "?"
            li = False
            try:
                ip = self.server.reaper.get_ip()
                li = self.server.reaper.check_login()
            except:
                pass
            self._json({
                "ok": True,
                "ip": ip,
                "logged_in": li,
                "busy": q.is_busy(),
                "active_jobs": q.active_job_count(),
                "pending_jobs": q.pending_job_count(),
                "total_jobs": len(q.list_jobs()),
                "workers": WORKER_COUNT,
            })
        elif self.path == "/ip":
            try:
                self._json({"ip": self.server.reaper.get_ip()})
            except Exception as e:
                self._json({"error": str(e)}, 500)
        elif self.path.startswith("/job/"):
            job_id = self.path.split("/")[-1]
            status = self.server.job_queue.get_status(job_id)
            if status:
                self._json(status)
            else:
                self._json({"error": "job_not_found"}, 404)
        elif self.path == "/jobs":
            limit = int(self.path.split("?limit=")[1]) if "?limit=" in self.path else 50
            self._json({"jobs": self.server.job_queue.list_jobs(limit=limit)})
        else:
            self._json({"error": "not_found"}, 404)

    def do_POST(self):
        clen = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(clen) if clen else b"{}"
        try:
            data = json.loads(body)
        except:
            data = {}
        if data.get("secret") != API_SECRET:
            return self._json({"error": "bad_secret"}, 403)

        q = self.server.job_queue

        if self.path == "/scrape":
            url = data.get("url", "")
            if not url:
                return self._json({"error": "no_url"}, 400)
            max_vids = data.get("max_videos", MAX_VIDEOS)
            job = Job("scrape", {"url": url, "max_videos": max_vids})
            job_id = q.submit(job)
            return self._json({"job_id": job_id, "status": "submitted", "url": url})

        elif self.path == "/report":
            vids = data.get("videos", [])
            if not vids:
                return self._json({"error": "no_videos"}, 400)
            comments = data.get("comments", "")
            job = Job("batch_report", {"videos": vids, "comments": comments})
            # Optional: wait for result if sync=true
            wait = data.get("sync", False)
            job_id = q.submit(job)
            if wait:
                job.wait(timeout=3600)
                return self._json({
                    "job_id": job_id,
                    "result": job.result,
                    "error": job.error,
                    "status": job.status,
                })
            return self._json({"job_id": job_id, "status": "submitted", "count": len(vids)})

        elif self.path == "/report_one":
            vid = data.get("video", "")
            if not vid:
                return self._json({"error": "no_video"}, 400)
            comments = data.get("comments", "")
            job = Job("batch_report", {"videos": [vid], "comments": comments})
            job_id = q.submit(job)
            job.wait(timeout=120)
            return self._json({
                "job_id": job_id,
                "video": vid,
                "result": job.result,
                "error": job.error,
                "status": job.status,
            })

        elif self.path == "/job_cancel":
            job_id = data.get("job_id", "")
            if not job_id:
                return self._json({"error": "no_job_id"}, 400)
            ok = q.cancel_job(job_id)
            self._json({"cancelled": ok, "job_id": job_id})

        elif self.path == "/job_result":
            job_id = data.get("job_id", "")
            if not job_id:
                return self._json({"error": "no_job_id"}, 400)
            job = q.get_job(job_id)
            if not job:
                return self._json({"error": "not_found"}, 404)
            return self._json({
                "job_id": job_id,
                "status": job.status,
                "result": job.result,
                "error": job.error,
            })

        elif self.path == "/stop":
            for j in q.list_jobs():
                q.cancel_job(j["id"])
            self._json({"ok": True, "message": "stop_sent"})

        else:
            self._json({"error": "not_found"}, 404)

    def _json(self, obj, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode())

    def log_message(self, fmt, *args):
        log.debug(f"HTTP: {args}")


# ============================================================
# WORKER POOL — Creates separate Reaper instances per worker
# ============================================================
class WorkerPool:
    """Manages multiple Reaper instances, one per worker thread."""

    def __init__(self, count: int):
        self.workers: List[Reaper] = []
        for i in range(count):
            profile = PROFILE_DIR if count <= 1 else f"{PROFILE_DIR}_worker{i}"
            self.workers.append(Reaper(profile, worker_id=i))
        self._index = 0
        self._lock = threading.Lock()

    def get_worker(self) -> Reaper:
        with self._lock:
            w = self.workers[self._index % len(self.workers)]
            self._index += 1
            return w

    def die_all(self):
        for w in self.workers:
            w.die()

    def check_login_any(self) -> bool:
        return any(w.check_login() for w in self.workers[:1])  # only check first


def worker_process(job: Job, pool: WorkerPool):
    """Dispatch a job to an available worker."""
    worker = pool.get_worker()
    worker.process_job(job)


# ============================================================
# TELEGRAM BOT
# ============================================================
class TelegramBot:
    def __init__(self, pool: WorkerPool, job_queue: JobQueue):
        self.pool = pool
        self.job_queue = job_queue
        self.loop = None

    async def cmd_start(self, upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        ip = "?"
        try:
            ip = await asyncio.get_event_loop().run_in_executor(None, self.pool.workers[0].get_ip)
        except:
            pass
        li = self.pool.workers[0].check_login()
        active = self.job_queue.active_job_count()
        pending = self.job_queue.pending_job_count()

        txt = (
            f"☠️ <b>YT REAPER</b>\n"
            f"IP: <code>{ip}</code>\n"
            f"Login: {'✅' if li else '❌'}\n"
            f"Workers: {WORKER_COUNT} | Active: {active} | Pending: {pending}\n"
            f"Profile: <code>{PROFILE_DIR}</code>\n\n"
            f"Scrape a channel → report videos via browser automation.\n"
            f"<b>Authorized pentest only.</b>"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📺 Reap Channel", callback_data="chan")],
            [InlineKeyboardButton("📹 Reap Single", callback_data="single")],
            [InlineKeyboardButton("📄 Reap from File", callback_data="file")],
            [InlineKeyboardButton("🔄 Status", callback_data="status")],
            [InlineKeyboardButton("🛑 Stop", callback_data="stop")],
        ])
        await upd.message.reply_text(txt, parse_mode="HTML", reply_markup=kb)
        return MENU

    async def cb_menu(self, upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        q = upd.callback_query
        await q.answer()
        d = q.data

        if d == "status":
            ip = "?"
            try:
                ip = await asyncio.get_event_loop().run_in_executor(None, self.pool.workers[0].get_ip)
            except:
                pass
            li = self.pool.workers[0].check_login()
            jobs = self.job_queue.list_jobs()
            active = sum(1 for j in jobs if j["status"] == Job.RUNNING)
            pending = sum(1 for j in jobs if j["status"] == Job.PENDING)
            done = sum(1 for j in jobs if j["status"] in (Job.DONE, Job.FAILED))
            txt = (
                f"🔄 <b>Status</b>\n"
                f"IP: <code>{ip}</code>\n"
                f"Login: {'✅' if li else '❌'}\n"
                f"Workers: {WORKER_COUNT}\n"
                f"Active: {active} | Pending: {pending} | Done: {done}\n"
                f"Total tracked: {len(jobs)}"
            )
            await q.edit_message_text(txt, parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="back")]]))
            return MENU

        elif d == "stop":
            for j in self.job_queue.list_jobs():
                self.job_queue.cancel_job(j["id"])
            await q.edit_message_text("🛑 Stop signal sent. All pending jobs cancelled.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="back")]]))
            return MENU

        elif d == "back":
            return await self.cmd_start(upd, ctx)

        elif d == "chan":
            await q.edit_message_text(
                "📺 Send channel URL:\n<code>https://youtube.com/@Handle</code>\n<code>https://youtube.com/channel/UC...</code>",
                parse_mode="HTML"
            )
            return AWAIT_TARGET

        elif d == "single":
            await q.edit_message_text("📹 Send video URL or ID:", parse_mode="HTML")
            return AWAIT_TARGET

        elif d == "file":
            await q.edit_message_text("📄 Upload .txt file (one URL per line):")
            return AWAIT_TARGET

        elif d == "confirm_yes":
            return await self._execute(upd, ctx)
        elif d == "confirm_no":
            await q.edit_message_text("❌ Cancelled.")
            return await self.cmd_start(upd, ctx)

        return MENU

    async def handle_text(self, upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        txt = upd.message.text.strip()

        if re.search(r'youtube\.com/(?:@|channel/|c/|user/)', txt):
            await upd.message.reply_text("🔍 Submitting scrape job...")
            job = Job("scrape", {"url": txt, "max_videos": MAX_VIDEOS})
            job_id = self.job_queue.submit(job)
            await upd.message.reply_text(
                f"📋 Scrape job submitted: <code>{job_id}</code>\n"
                f"Scraping in background. You'll be notified when done.",
                parse_mode="HTML"
            )
            asyncio.ensure_future(self._wait_scrape_and_confirm(upd, ctx, job, job_id))
            return MENU
        else:
            vid = extract_video_id(txt)
            if not vid:
                await upd.message.reply_text("❌ Not a valid video URL/ID.")
                return AWAIT_TARGET
            ctx.user_data["vids"] = [vid]
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Reap", callback_data="confirm_yes")],
                [InlineKeyboardButton("❌ Cancel", callback_data="confirm_no")],
            ])
            await upd.message.reply_text(
                f"🎯 <code>{vid}</code>\nhttps://youtu.be/{vid}\n\nProceed?",
                parse_mode="HTML", reply_markup=kb
            )
            return AWAIT_CONFIRM

    async def _wait_scrape_and_confirm(self, upd: Update, ctx: ContextTypes.DEFAULT_TYPE, job: Job, job_id: str):
        try:
            result = await asyncio.get_event_loop().run_in_executor(None, job.wait, 300)
            if result is None:
                await ctx.bot.send_message(
                    chat_id=upd.effective_chat.id,
                    text=f"❌ Scrape job <code>{job_id}</code> timed out or failed.",
                    parse_mode="HTML"
                )
                return
            vids = result.get("videos", [])
            if not vids:
                await ctx.bot.send_message(
                    chat_id=upd.effective_chat.id,
                    text="❌ No videos found. Channel empty or unreachable."
                )
                return
            ctx.user_data["vids"] = vids
            preview = ", ".join(f"<code>{v}</code>" for v in vids[:8])
            rest = f"\n... and {len(vids)-8} more" if len(vids) > 8 else ""
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(f"✅ Reap {len(vids)} videos", callback_data="confirm_yes")],
                [InlineKeyboardButton("❌ Cancel", callback_data="confirm_no")],
            ])
            await ctx.bot.send_message(
                chat_id=upd.effective_chat.id,
                text=f"🎯 <b>{len(vids)} videos</b>\n{preview}{rest}\n\nProceed?",
                parse_mode="HTML", reply_markup=kb
            )
        except Exception as e:
            await ctx.bot.send_message(
                chat_id=upd.effective_chat.id,
                text=f"❌ Error: {e}"
            )

    async def handle_file(self, upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        doc = upd.message.document
        if not doc or not doc.file_name.endswith(".txt"):
            await upd.message.reply_text("Send a .txt file.")
            return AWAIT_TARGET
        file = await doc.get_file()
        raw = await file.download_as_bytearray()
        vids = []
        for line in raw.decode("utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                v = extract_video_id(line)
                if v:
                    vids.append(v)
        if not vids:
            await upd.message.reply_text("❌ No valid video IDs in file.")
            return AWAIT_TARGET
        ctx.user_data["vids"] = vids
        preview = ", ".join(f"<code>{v}</code>" for v in vids[:8])
        rest = f"\n... and {len(vids)-8} more" if len(vids) > 8 else ""
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"✅ Reap {len(vids)} videos", callback_data="confirm_yes")],
            [InlineKeyboardButton("❌ Cancel", callback_data="confirm_no")],
        ])
        await upd.message.reply_text(
            f"📄 <b>{len(vids)} videos</b>\n{preview}{rest}\n\nProceed?",
            parse_mode="HTML", reply_markup=kb
        )
        return AWAIT_CONFIRM

    async def _execute(self, upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        q = upd.callback_query
        if q:
            await q.answer()
        vids = ctx.user_data.get("vids", [])
        if not vids:
            if q:
                await q.edit_message_text("Nothing to do.")
            return await self.cmd_start(upd, ctx)

        if q:
            await q.edit_message_text(f"🔄 Submitting report job for {len(vids)} videos...")

        chat_id = upd.effective_chat.id
        job = Job("batch_report", {"videos": vids, "comments": ""})
        job_id = self.job_queue.submit(job)

        await ctx.bot.send_message(
            chat_id=chat_id,
            text=f"📋 Report job submitted: <code>{job_id}</code>\nRunning in background...",
            parse_mode="HTML"
        )

        async def prog(idx, total, vid, ok, msg):
            try:
                icon = "✅" if ok else "❌"
                await ctx.bot.send_message(
                    chat_id=chat_id,
                    text=f"{icon} [{idx}/{total}] <code>{vid}</code>: {msg}",
                    parse_mode="HTML"
                )
            except:
                pass

        def sync_prog(idx, total, vid, ok, msg):
            if self.loop and self.loop.is_running():
                asyncio.run_coroutine_threadsafe(prog(idx, total, vid, ok, msg), self.loop)

        job.progress_callback = sync_prog

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, job.wait, 3600)
            if result is None:
                await ctx.bot.send_message(chat_id=chat_id, text="❌ Job timed out or failed.")
                return await self.cmd_start(upd, ctx)
        except Exception as e:
            await ctx.bot.send_message(chat_id=chat_id, text=f"❌ Job error: {e}")
            return await self.cmd_start(upd, ctx)

        s = len(result.get("successful", []))
        f = len(result.get("failed", []))
        summary = f"📊 <b>Done</b>\nTotal: {s+f} | ✅ {s} | ❌ {f}"
        if result.get("failed"):
            f_lines = []
            for vid, reason in result["failed"][:8]:
                f_lines.append(f"<code>{vid}</code>: {reason}")
            if len(result["failed"]) > 8:
                f_lines.append(f"... and {len(result['failed'])-8} more")
            summary += "\n\n<b>Failures:</b>\n" + "\n".join(f_lines)

        await ctx.bot.send_message(chat_id=chat_id, text=summary, parse_mode="HTML")
        ctx.user_data["vids"] = []
        return await self.cmd_start(upd, ctx)

    async def cancel(self, upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        await upd.message.reply_text("Cancelled.")
        return await self.cmd_start(upd, ctx)

    async def err_handler(self, upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
        log.error(f"TG error: {ctx.error}")


async def cmd_status(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pool = ctx.application.bot_data.get("pool")
    queue = ctx.application.bot_data.get("job_queue")
    if not pool or not queue:
        await upd.message.reply_text("❌ System not initialized.")
        return
    li = pool.workers[0].check_login()
    jobs = queue.list_jobs()
    active = sum(1 for j in jobs if j["status"] == Job.RUNNING)
    pending = sum(1 for j in jobs if j["status"] == Job.PENDING)
    done = sum(1 for j in jobs if j["status"] in (Job.DONE, Job.FAILED))
    txt = (f"🔄 <b>YT Reaper Status</b>\n"
           f"Login: {'✅' if li else '❌'}\n"
           f"Workers: {WORKER_COUNT}\n"
           f"Active: {active} | Pending: {pending} | Done: {done}\n")
    for j in jobs[:5]:
        txt += f"  <code>{j['id']}</code>: {j['type']} [{j['status']}]\n"
    await upd.message.reply_text(txt, parse_mode="HTML")


# ============================================================
# MAIN
# ============================================================
def main():
    print("=" * 60)
    print("YT REAPER v3 - Channel Video Reporter")
    print("=" * 60)
    print(f"[*] BOT_TOKEN loaded: {BOT_TOKEN[:8]}...{BOT_TOKEN[-4:]}")

    if not SELENIUM_OK:
        print("FATAL: Missing dependencies.")
        print("  pip install undetected-chromedriver selenium python-telegram-bot")
        sys.exit(1)

    # Initialize
    job_queue = JobQueue(worker_count=WORKER_COUNT, ttl_hours=JOB_TTL_HOURS)
    pool = WorkerPool(WORKER_COUNT)

    # Start worker threads
    def worker_fn(job):
        worker_process(job, pool)
    job_queue.start(worker_fn)

    # Start HTTP API thread
    api_server = ThreadingHTTPServer(("0.0.0.0", API_PORT), APIHandler)
    api_server.job_queue = job_queue
    api_server.reaper = pool.workers[0]  # primary worker for status checks
    api_thread = threading.Thread(target=api_server.serve_forever, daemon=True)
    api_thread.start()
    print(f"[*] HTTP API on :{API_PORT} (threaded, {WORKER_COUNT} worker(s))")
    print(f"[*] Chrome profiles: {PROFILE_DIR}{'[0-'+str(WORKER_COUNT-1)+']' if WORKER_COUNT>1 else ''}")
    print(f"[*] Max videos: {MAX_VIDEOS}, delay: {DELAY_MIN}-{DELAY_MAX}s")
    print(f"[*] Page load timeout: {PAGE_LOAD_TIMEOUT}s, job TTL: {JOB_TTL_HOURS}h")
    print()

    # Check login
    if not pool.check_login_any():
        print("⚠️  NOT LOGGED INTO YOUTUBE!")
        print(f"   Open Chrome: google-chrome --user-data-dir={PROFILE_DIR}")
        print("   Go to youtube.com, log in, close Chrome, restart this script.")
        if WORKER_COUNT > 1:
            for i in range(1, WORKER_COUNT):
                print(f"   Repeat for: google-chrome --user-data-dir={PROFILE_DIR}_worker{i}")
        print()

    # Telegram bot
    app = Application.builder().token(BOT_TOKEN).build()
    app.bot_data["pool"] = pool
    app.bot_data["job_queue"] = job_queue

    bot = TelegramBot(pool, job_queue)

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", bot.cmd_start)],
        states={
            MENU: [CallbackQueryHandler(bot.cb_menu)],
            AWAIT_TARGET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_text),
                MessageHandler(filters.Document.FileExtension("txt"), bot.handle_file),
                CommandHandler("cancel", bot.cancel),
            ],
            AWAIT_CONFIRM: [CallbackQueryHandler(bot.cb_menu)],
        },
        fallbacks=[
            CommandHandler("start", bot.cmd_start),
            CommandHandler("cancel", bot.cancel),
            CommandHandler("status", cmd_status),
        ],
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_error_handler(bot.err_handler)
    bot.loop = app.loop

    def shutdown(sig, frame):
        log.info(f"Signal {sig}, shutting down...")
        job_queue.stop()
        pool.die_all()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    print("[*] Bot running. Press Ctrl+C to stop.")
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        job_queue.stop()
        pool.die_all()
        print("Shutdown.")


if __name__ == "__main__":
    main()
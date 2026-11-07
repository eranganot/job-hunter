"""Apply-engine rehearsal (Step 8, 2026-11-07).

Runs the REAL apply_engine.submit_application against a REAL posting page while
every request that would send data (anything but GET) is captured and ABORTED -
except Google reCAPTCHA, which the form needs before it submits. A self-check
proves the guard before the engine runs, and the applicant is made up
("Rehearsal Test", example.com, a dummy CV), so even a guard failure could not
send an application in a real person's name.

    python3 scripts/rehearse_apply.py <posting url> <out dir>
    FAKE_S3=1      answer Greenhouse's direct-to-S3 CV upload with a fake success
    STUB_GEMINI=1  stand in for Gemini on the required-question step (no key needed)

Writes <out dir>/rehearsal.json (the engine's result and every captured request,
i.e. exactly what would have reached the employer) and two screenshots.
"""
import json, os, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from playwright.sync_api import sync_playwright
import playwright.sync_api._generated as G
import apply_engine as ae

URL = sys.argv[1]
OUT = sys.argv[2]
CAPTURED, SHOTS = [], []
# Captcha and bot-check services, not the employer: the form cannot submit without them.
ALLOW_WRITE = ("google.com/recaptcha", "recaptcha.net", "gstatic.com", "hcaptcha.com",
               "/cdn-cgi/challenge-platform/")

def guard(route, req):
    if req.method == "POST" and "s3.amazonaws.com" in req.url and os.environ.get("FAKE_S3"):
        # Greenhouse uploads the chosen file straight to S3; answer it as S3 would.
        CAPTURED.append({"method": "POST(fake-ok)", "url": req.url, "bytes": len(req.post_data_buffer or b""), "body_head": ""})
        key = "rehearsal/cv.pdf"
        return route.fulfill(status=201, headers={"Content-Type": "application/xml", "Access-Control-Allow-Origin": "*"},
            body="<PostResponse><Location>%s%s</Location><Bucket>b</Bucket><Key>%s</Key><ETag>\"x\"</ETag></PostResponse>" % (req.url, key, key))
    if req.method == "POST" and req.url.endswith("/parseResume") and os.environ.get("FAKE_S3"):
        # Lever parses the attached CV before submit; answer as an empty parse.
        CAPTURED.append({"method": "POST(fake-ok)", "url": req.url, "bytes": len(req.post_data_buffer or b""), "body_head": ""})
        return route.fulfill(status=200, content_type="application/json", body="{}")
    if req.method != "GET" and not any(h in req.url for h in ALLOW_WRITE):
        try: body = req.post_data_buffer or b""
        except Exception: body = b""
        CAPTURED.append({"method": req.method, "url": req.url, "bytes": len(body),
                         "body_head": body[:3000].decode("utf-8", "replace")})
        return route.abort()
    return route.continue_()

_orig_launch = G.BrowserType.launch
def launch(self, **kw):
    if os.path.exists("/usr/bin/google-chrome"):
        kw["executable_path"] = "/usr/bin/google-chrome"
    return _orig_launch(self, **kw)
G.BrowserType.launch = launch
_orig_ctx = G.Browser.new_context
def new_context(self, **kw):
    ctx = _orig_ctx(self, **kw)
    ctx.route("**/*", guard)
    ctx.on("page", lambda p: SHOTS.append(p))
    return ctx
G.Browser.new_context = new_context

# Safety self-check BEFORE the engine runs: a POST to Greenhouse must be captured.
with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True, args=["--no-sandbox"])
    c = b.new_context(); p = c.new_page()
    p.goto(URL, wait_until="domcontentloaded")
    p.evaluate("fetch(location.origin + '/selfcheck', {method:'POST', body:'x'}).catch(()=>0)")
    p.wait_for_timeout(1500); b.close()
assert any("selfcheck" in x["url"] for x in CAPTURED), "GUARD FAILED - aborting rehearsal"
CAPTURED.clear(); print("guard self-check: OK (POST captured and aborted)")

os.makedirs(OUT, exist_ok=True)
cv = os.path.join(OUT, "rehearsal_cv.pdf")
sys.path.insert(0, os.path.join(ROOT, "tests"))
from test_admin_users import _tiny_pdf
open(cv, "wb").write(_tiny_pdf(["Rehearsal Test", "rehearsal@example.com", "Test CV for an apply-engine rehearsal. " * 3]))
applicant = {"full_name": "Rehearsal Test", "first_name": "Rehearsal", "last_name": "Test",
             "email": "rehearsal@example.com", "phone": ae.intl_phone("050-0000000"),
             "linkedin_url": "https://www.linkedin.com/in/rehearsal-example/", "location": "Tel Aviv, Israel"}

# Screenshot the page at the moment the engine finalises.
_orig_fin = ae._finalize_after_submit
def fin(page, applicant, sels):
    page.screenshot(path=os.path.join(OUT, "after_submit.png"), full_page=True)
    r = _orig_fin(page, applicant, sels)
    page.screenshot(path=os.path.join(OUT, "after_finalize.png"), full_page=True)
    return r
ae._finalize_after_submit = fin

FIELDS_SEEN = []
if os.environ.get("STUB_GEMINI"):
    ae.GEMINI_KEY = "stub"
    def stub(prompt, **k):
        # A stand-in for Gemini on the question step (questions.py's prompt):
        # sure about the obvious, deliberately UNSURE about citizenship, so a
        # rehearsal shows both the fill path and the hold-for-the-applicant path.
        if "ON BEHALF" in prompt:
            qs = json.loads(prompt.split("Questions (JSON array): ", 1)[1].split("\n\nReturn ONLY", 1)[0])
        elif "REQUIRED fields" in prompt:
            qs = json.loads(prompt.split("Fields (JSON array):\n", 1)[1].split("\n\nReturn ONLY", 1)[0])
        else:
            raise RuntimeError("stub: unexpected prompt")
        FIELDS_SEEN.extend(qs)
        acts = []
        for f in qs:
            lab = (f.get("label") or "").lower(); sel = f.get("selector"); opts = f.get("options") or []
            pick = lambda *w: next((o for o in opts if any(x in str(o).lower() for x in w)), None)
            if "city" in lab or "location" in lab:
                acts.append({"selector": sel, "value": "Tel Aviv, Israel", "confidence": "high", "action": "fill"})
            elif "sponsor" in lab or "visa" in lab:
                acts.append({"selector": sel, "value": pick("no") or "No", "confidence": "high", "action": "select"})
            elif "authorized" in lab:
                acts.append({"selector": sel, "value": pick("yes") or "Yes", "confidence": "high", "action": "select"})
            elif "hear" in lab and opts:
                acts.append({"selector": sel, "value": pick("website", "career") or opts[0], "confidence": "high"})
            elif "consent" in " ".join(map(str, opts)).lower():
                acts.append({"selector": sel, "value": pick("yes") or opts[0], "confidence": "high"})
            elif f.get("type") == "checkbox" and pick("english"):
                acts.append({"selector": sel, "value": [pick("english")], "confidence": "high"})
            elif sel:
                acts.append({"selector": sel, "value": (opts[0] if opts else "draft answer"), "confidence": "low"})
        return acts
    ae._claude_json = stub

t0 = time.time()
os.makedirs(OUT, exist_ok=True)
res = ae.submit_application(URL, os.environ.get("JOB_TITLE", "Rehearsal"), os.environ.get("COMPANY", "Rehearsal"),
                            applicant, cv, force=True)
print("seconds:", round(time.time() - t0, 1))
print("RESULT:", json.dumps(res, indent=1)[:1500])
print("FIELDS the engine collected:", json.dumps(FIELDS_SEEN)[:1500])
print("CAPTURED writes:", len(CAPTURED))
for x in CAPTURED: print(" ", x["method"], x["url"][:120], x["bytes"], "bytes")
json.dump({"result": res, "captured": CAPTURED}, open(os.path.join(OUT, "rehearsal.json"), "w"), indent=1)

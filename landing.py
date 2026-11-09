"""
landing.py - the public page at the bare domain, for signed-out visitors.

Step 10 of EXECUTION_PLAN_NEXT.md (2026-11-09). Until now job-hunter.work sent a
stranger straight to a sign-in form. Self-contained like /login: no external
stylesheet, no script, no fonts - palette matched to /app by value.

Every claim on this page has to be true of the product today. In particular,
auto-apply is described as in testing (it runs for admins only), and the price
line follows plans.NEW_USER_PLAN (every signup gets Beta, free).
"""
import html

_MARK_SVG = ('<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" '
             'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
             '<rect x="2" y="7" width="20" height="14" rx="2"/>'
             '<path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/></svg>')

TITLE = "Job Hunter - find the roles worth applying to"
DESCRIPTION = ("Job Hunter searches job boards and company career pages for the roles you want, "
               "scores each one against your CV and tells you why, and gets every application ready.")

_CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{--bg:#0f172a;--card:#111827;--line:#1f2937;--line2:#374151;--text:#e5e7eb;--muted:#9ca3af;
      --white:#fff;--accent:#6366f1;--accent2:#4f46e5;--accentText:#a5b4fc;--good:#34d399}
html{scroll-behavior:smooth}
body{margin:0;color:var(--text);background:var(--bg);
     background-image:radial-gradient(60rem 40rem at 10% -10%,rgba(99,102,241,.20),transparent 60%),
                      radial-gradient(50rem 40rem at 110% 30%,rgba(56,89,248,.12),transparent 60%);
     font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;line-height:1.55}
a{color:var(--accentText)}
.shell{max-width:68rem;margin:0 auto;padding:0 1rem}
@media(min-width:40rem){.shell{padding:0 1.5rem}}
nav{display:flex;align-items:center;justify-content:space-between;padding:1.1rem 0}
.brand{display:flex;align-items:center;gap:.65rem;color:var(--white);text-decoration:none;font-weight:700;font-size:1.05rem}
.mark{width:2.25rem;height:2.25rem;border-radius:.7rem;display:flex;align-items:center;justify-content:center;
      background:linear-gradient(135deg,#6366f1,#4338ca)}
.mark svg{width:1.15rem;height:1.15rem}
.navlinks{display:flex;align-items:center;gap:.5rem}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:.4rem;padding:.65rem 1.1rem;border-radius:.75rem;
     font-weight:600;font-size:.92rem;text-decoration:none;border:1px solid transparent;white-space:nowrap}
.btn.primary{color:#fff;background:linear-gradient(90deg,var(--accent),var(--accent2))}
.btn.primary:hover{filter:brightness(1.08)}
.btn.ghost{color:var(--text);border-color:var(--line2);background:rgba(17,24,39,.6)}
.btn.ghost:hover{background:#1f2937}
.btn.big{padding:.85rem 1.4rem;font-size:1rem}
.hero{display:grid;gap:2.5rem;align-items:center;padding:2.5rem 0 3.5rem}
@media(min-width:56rem){.hero{grid-template-columns:1.1fr .9fr;padding:4.5rem 0 5rem}}
.pill{display:inline-block;font-size:.78rem;font-weight:600;color:var(--accentText);background:rgba(99,102,241,.12);
      border:1px solid rgba(99,102,241,.35);border-radius:999px;padding:.25rem .7rem;margin-bottom:1rem}
h1{font-size:clamp(2rem,5.5vw,3.25rem);line-height:1.1;letter-spacing:-.02em;color:var(--white);margin:0 0 1rem}
h1 em{font-style:normal;background:linear-gradient(90deg,#a5b4fc,#818cf8);-webkit-background-clip:text;background-clip:text;color:transparent}
.lead{font-size:1.08rem;color:#cbd5e1;max-width:34rem;margin:0 0 1.6rem}
.ctas{display:flex;flex-wrap:wrap;gap:.6rem;align-items:center}
.fine{font-size:.82rem;color:var(--muted);margin-top:.85rem}
.demo{background:rgba(17,24,39,.85);border:1px solid var(--line2);border-radius:1.25rem;padding:1.1rem;
      box-shadow:0 30px 80px -30px rgba(0,0,0,.6)}
.demo .row{display:flex;justify-content:space-between;align-items:flex-start;gap:.75rem}
.demo .t{color:var(--white);font-weight:700;font-size:1.05rem}
.demo .c{color:var(--muted);font-size:.86rem}
.score{flex-shrink:0;font-weight:800;color:var(--good);font-size:1.35rem;line-height:1}
.score small{display:block;font-size:.68rem;font-weight:600;color:var(--muted);text-align:right;margin-top:.2rem}
.chips{display:flex;flex-wrap:wrap;gap:.35rem;margin:.8rem 0}
.chip{font-size:.72rem;font-weight:600;padding:.2rem .55rem;border-radius:999px;border:1px solid var(--line2);color:#cbd5e1}
.chip.ok{color:var(--good);border-color:rgba(52,211,153,.4);background:rgba(52,211,153,.08)}
.why{font-size:.84rem;color:#cbd5e1;background:#0b1220;border:1px solid var(--line);border-radius:.8rem;padding:.75rem .85rem}
.why b{color:var(--white)}
.why ul{margin:.4rem 0 0;padding-left:1.1rem}
.why li{margin:.15rem 0}
.acts{display:grid;grid-template-columns:1fr 1fr 1fr;gap:.45rem;margin-top:.85rem}
.acts span{text-align:center;font-size:.8rem;font-weight:600;padding:.55rem;border-radius:.65rem;border:1px solid var(--line2);color:#cbd5e1}
.acts span.go{color:#fff;background:linear-gradient(90deg,var(--accent),var(--accent2));border-color:transparent}
section{padding:3rem 0;border-top:1px solid var(--line)}
h2{font-size:clamp(1.45rem,3.5vw,1.9rem);color:var(--white);letter-spacing:-.01em;margin:0 0 .5rem}
.sub{color:var(--muted);margin:0 0 2rem;max-width:40rem}
.steps{list-style:none;margin:0;padding:0;display:grid;gap:1rem;counter-reset:s}
@media(min-width:48rem){.steps{grid-template-columns:repeat(4,1fr)}}
.steps li{counter-increment:s;background:rgba(17,24,39,.6);border:1px solid var(--line);border-radius:1rem;padding:1.1rem}
.steps li::before{content:counter(s);display:flex;align-items:center;justify-content:center;width:1.9rem;height:1.9rem;
      border-radius:.6rem;background:rgba(99,102,241,.16);color:var(--accentText);font-weight:700;margin-bottom:.7rem}
.steps h3,.grid h3{color:var(--white);font-size:1rem;margin:0 0 .35rem}
.steps p,.grid p{margin:0;font-size:.9rem;color:#cbd5e1}
.grid{display:grid;gap:1rem}
@media(min-width:40rem){.grid{grid-template-columns:1fr 1fr}}
@media(min-width:60rem){.grid{grid-template-columns:repeat(3,1fr)}}
.grid > div{background:rgba(17,24,39,.6);border:1px solid var(--line);border-radius:1rem;padding:1.1rem}
.price{display:grid;gap:1.5rem;align-items:center;background:linear-gradient(135deg,rgba(99,102,241,.16),rgba(17,24,39,.7));
       border:1px solid rgba(99,102,241,.35);border-radius:1.25rem;padding:1.5rem}
@media(min-width:48rem){.price{grid-template-columns:1fr auto;padding:2rem}}
.price ul{margin:.6rem 0 0;padding-left:1.1rem;color:#cbd5e1;font-size:.92rem}
footer{border-top:1px solid var(--line);padding:1.75rem 0 2.5rem;color:var(--muted);font-size:.85rem;
       display:flex;flex-wrap:wrap;gap:.75rem 1.5rem;justify-content:space-between}
footer a{color:var(--muted)}
footer a:hover{color:var(--text)}
"""


def landing_html(public_url: str = "") -> str:
    url = html.escape((public_url or "").rstrip("/") + "/", quote=True)
    t = html.escape(TITLE, quote=True)
    d = html.escape(DESCRIPTION, quote=True)
    canon = f'<link rel="canonical" href="{url}"/>' if public_url else ""
    og_url = f'<meta property="og:url" content="{url}"/>' if public_url else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover"/>
  <meta name="theme-color" content="#0f172a"/>
  <title>{t}</title>
  <meta name="description" content="{d}"/>
  {canon}
  <meta property="og:type" content="website"/>
  <meta property="og:site_name" content="Job Hunter"/>
  <meta property="og:title" content="{t}"/>
  <meta property="og:description" content="{d}"/>
  {og_url}
  <meta name="twitter:card" content="summary"/>
  <meta name="twitter:title" content="{t}"/>
  <meta name="twitter:description" content="{d}"/>
  <link rel="manifest" href="/app/manifest.webmanifest"/>
  <style>{_CSS}</style>
</head>
<body>
<div class="shell">
  <nav>
    <a class="brand" href="/"><span class="mark">{_MARK_SVG}</span>Job Hunter</a>
    <div class="navlinks">
      <a class="btn ghost" href="/login">Sign in</a>
      <a class="btn primary" href="/register">Start free</a>
    </div>
  </nav>

  <header class="hero">
    <div>
      <span class="pill">Free while in beta</span>
      <h1>Find the roles <em>worth applying to</em>. Get every application ready.</h1>
      <p class="lead">Job Hunter searches job boards and company career pages for the roles you want,
        scores each one against your CV and tells you why, and prepares the application:
        a CV tailored to the job, a cover letter, and your answers ready to paste.</p>
      <div class="ctas">
        <a class="btn primary big" href="/register">Start free</a>
        <a class="btn ghost big" href="#how">How it works</a>
      </div>
      <p class="fine">Sign up with Google or email. No card needed.</p>
    </div>

    <div class="demo" aria-label="Example of a matched job">
      <div class="row">
        <div><div class="t">Senior Product Manager</div><div class="c">Northwind Labs&nbsp;&middot; Tel Aviv&nbsp;&middot; Hybrid</div></div>
        <div class="score">92%<small>match</small></div>
      </div>
      <div class="chips"><span class="chip ok">Company application page</span><span class="chip">Posted 2 days ago</span></div>
      <div class="why"><b>Why this fits</b>
        <ul>
          <li>6 years leading B2C product teams - they ask for 5+</li>
          <li>Personalization and experimentation, both in your CV</li>
          <li>Tel Aviv, inside the area you chose</li>
        </ul>
      </div>
      <div class="acts"><span>Pass</span><span>Later</span><span class="go">Approve</span></div>
    </div>
  </header>

  <section id="how">
    <h2>How it works</h2>
    <p class="sub">Five minutes to set up. After that it runs on its own and you decide what goes out.</p>
    <ol class="steps">
      <li><h3>Upload your CV</h3><p>It reads your CV and fills in the titles, keywords and locations to search for. Change anything you like.</p></li>
      <li><h3>It searches for you</h3><p>Job boards plus companies' own career pages, on a schedule. Dead and duplicate links are dropped.</p></li>
      <li><h3>Swipe through matches</h3><p>Each job comes with a score and the reasons behind it. Approve, pass or save it for later.</p></li>
      <li><h3>Apply, prepared</h3><p>Open the application with a tailored CV, a cover letter and your answers ready. Your pass reasons sharpen the next search.</p></li>
    </ol>
  </section>

  <section>
    <h2>What you get</h2>
    <p class="sub">Built to save the hours between "I should apply" and actually applying.</p>
    <div class="grid">
      <div><h3>Scores you can read</h3><p>Not a black box: every match lists what lines up with your CV and what doesn't.</p></div>
      <div><h3>Links that go somewhere</h3><p>It prefers the employer's own application page over a job-board repost, and checks the link still works.</p></div>
      <div><h3>A CV for each job</h3><p>Rewritten around the job's requirements, using only what is already in your CV. Every claim is checked against it.</p></div>
      <div><h3>Cover letters and answers</h3><p>A cover letter per job, and the answers to common application questions saved once and reused.</p></div>
      <div><h3>Told when it matters</h3><p>New matches by push, email, Telegram or WhatsApp - whichever you pick.</p></div>
      <div><h3>Applying for you - in testing</h3><p>On application systems it supports, Job Hunter can fill in and submit the form itself. It's being tested with a small group first.</p></div>
    </div>
  </section>

  <section>
    <div class="price">
      <div>
        <h2>Free while in beta</h2>
        <p class="sub" style="margin:0">Every new account gets the full beta plan, at no cost:</p>
        <ul>
          <li>Daily searches, plus searches on demand</li>
          <li>Tailored CVs and cover letters</li>
          <li>Full analytics on your search</li>
        </ul>
      </div>
      <a class="btn primary big" href="/register">Create your account</a>
    </div>
  </section>

  <footer>
    <span>&copy; 2026 Job Hunter</span>
    <span><a href="/login">Sign in</a>&nbsp;&middot; <a href="/register">Create account</a>&nbsp;&middot; <a href="mailto:support@job-hunter.work">support@job-hunter.work</a></span>
  </footer>
</div>
</body>
</html>"""

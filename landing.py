"""
landing.py - the public page at the bare domain, for signed-out visitors.

Rebuilt 2026-11-11 for job seekers (the first version, Step 10, read like a
product sheet). Positioning: "your personal AI headhunter agent - it works for
you, not for the companies". Voice: "we" is Job Hunter, "your agent" is what
does the work. The offer is "Premium is free for a limited time"; the word
"beta" does not appear on the page.

Design language is the app's own: dark only, the same slate surfaces, the same
system type, the app's icon as the logo. The one thing that differs is the
accent: the app's indigo stays in the logo and in the product screens, and the
page's buttons use ACCENT (the owner: the purple reads like every other AI
tool).

Product pictures are real: screenshots of /app run against a demo account
with invented companies (assets/landing/, served at /landing-assets/). No
drawn imitations of the interface.

Self-contained like /login: no external stylesheet, no script.

Every claim on this page has to be true of the product today:
- the search covers tech roles in Israel;
- applying for you is described as being switched on account by account (the
  engine runs for admins only);
- the activity line is drawn from the latest search's own numbers and is left
  out when there are none (see landing_html's `postings_checked`).
"""
import html

TITLE = "Job Hunter - your personal AI headhunter agent"
DESCRIPTION = ("Meet your personal AI headhunter agent for tech roles in Israel. It searches company career "
               "pages and job boards every day, shortlists the roles that fit your CV, and gets each "
               "application ready.")

# name -> (accent, hover, text on the accent)
ACCENTS = {
    "amber":   ("#fbbf24", "#f59e0b", "#0f172a"),
    "emerald": ("#34d399", "#10b981", "#0f172a"),
    "coral":   ("#fb7185", "#f43f5e", "#0f172a"),
}
ACCENT = "amber"

_CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{--bg:#0f172a;--card:#1e293b;--line:#334155;--ink:#f8fafc;--body:#cbd5e1;--muted:#94a3b8;
      --accent:__ACCENT__;--accent2:__ACCENT2__;--on:__ON__}
html{scroll-behavior:smooth}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
body{margin:0;color:var(--body);background:var(--bg);font-size:1.02rem;line-height:1.6;
     font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif}
a{color:var(--accent)}
a:focus-visible,summary:focus-visible{outline:3px solid var(--accent);outline-offset:3px;border-radius:.4rem}
.wrap{max-width:70rem;margin:0 auto;padding-left:1.15rem;padding-right:1.15rem}
@media(min-width:48rem){.wrap{padding-left:2rem;padding-right:2rem}}
h1,h2,h3{color:var(--ink);margin:0}
h1{font-size:clamp(2.2rem,5.6vw,3.7rem);line-height:1.06;letter-spacing:-.03em;font-weight:800}
h2{font-size:clamp(1.65rem,3.8vw,2.4rem);line-height:1.12;letter-spacing:-.02em;font-weight:800;max-width:24ch}
h3{font-size:1.12rem;line-height:1.3;font-weight:700}
p{margin:0}

.top{display:flex;align-items:center;justify-content:space-between;gap:.6rem;padding-top:1.1rem;padding-bottom:1.1rem}
.brand{display:flex;align-items:center;gap:.65rem;color:var(--ink);text-decoration:none;font-weight:700;
       font-size:1.12rem;white-space:nowrap}
.brand img{width:2.2rem;height:2.2rem;border-radius:.6rem;display:block}
.toplinks{display:flex;align-items:center;gap:.2rem}
.btn{display:inline-flex;align-items:center;justify-content:center;padding:.7rem 1.15rem;border-radius:.8rem;
     font-weight:700;font-size:.95rem;text-decoration:none;border:1px solid transparent;white-space:nowrap}
.btn.go{color:var(--on);background:var(--accent)}
.btn.go:hover{background:var(--accent2)}
.btn.quiet{color:var(--body)}
.btn.quiet:hover{color:var(--ink)}
.btn.line{color:var(--ink);border-color:var(--line);background:var(--card)}
.btn.line:hover{border-color:var(--muted)}
.btn.big{padding:.95rem 1.6rem;font-size:1.05rem}
@media(max-width:26rem){.toplinks .btn{padding:.5rem .7rem;font-size:.84rem}.brand{font-size:1rem;gap:.45rem}
  .brand img{width:1.9rem;height:1.9rem}}

.hero{display:grid;gap:2.5rem;padding-top:2rem;padding-bottom:3.5rem;align-items:center}
@media(min-width:60rem){.hero{grid-template-columns:1.15fr .85fr;gap:3rem;padding-top:3.5rem;padding-bottom:5rem}}
.hero .lead{font-size:1.18rem;line-height:1.55;max-width:34rem;margin:1.4rem 0 1.8rem}
.ctas{display:flex;flex-wrap:wrap;gap:.7rem;align-items:center}
.offer-line{margin-top:1.1rem;font-size:.95rem;color:var(--muted)}
.offer-line b{color:var(--accent)}

.phone{margin:0 auto;width:min(19.5rem,100%);border:1px solid var(--line);border-radius:2.2rem;padding:.55rem;
       background:#020617;box-shadow:0 30px 60px -20px rgba(2,6,23,.9)}
.phone img{display:block;width:100%;height:auto;border-radius:1.7rem}
.phone figcaption{font-size:.82rem;color:var(--muted);text-align:center;padding:.6rem .4rem .2rem}
figure{margin:0}

.proof{border-top:1px solid var(--line);border-bottom:1px solid var(--line);background:#0b1222}
.proof p{font-weight:700;color:var(--ink);font-size:clamp(1.15rem,2.4vw,1.5rem);line-height:1.35;
         padding-top:1.5rem;padding-bottom:1.5rem;max-width:46rem}
.proof b{color:var(--accent)}

.sec{padding-top:3.6rem;padding-bottom:3.6rem}
@media(min-width:48rem){.sec{padding-top:5rem;padding-bottom:5rem}}
.sec.tight{padding-top:0}
.intro{margin:.9rem 0 2.2rem;max-width:38rem}

.vs{display:grid;gap:1rem}
@media(min-width:48rem){.vs{grid-template-columns:1fr 1fr;gap:1.4rem}}
.vs > div{border-radius:1rem;padding:1.4rem;border:1px solid var(--line)}
.vs .them{background:transparent}
.vs .us{background:var(--card);border-color:var(--accent)}
.vs h3{margin-bottom:.7rem}
.vs ul{margin:0;padding:0;list-style:none}
.vs li{padding:.55rem 0;border-top:1px solid var(--line)}
.vs li:first-child{border-top:0;padding-top:0}
.vs .them li{color:var(--muted)}

.steps{list-style:none;margin:0;padding:0;display:grid;gap:1.2rem;counter-reset:s}
@media(min-width:52rem){.steps{grid-template-columns:repeat(3,1fr);gap:1.4rem}}
.steps li{counter-increment:s;background:var(--card);border:1px solid var(--line);border-radius:1rem;padding:1.3rem}
.steps li::before{content:counter(s);display:flex;align-items:center;justify-content:center;width:2.2rem;height:2.2rem;
                  border-radius:.7rem;background:var(--accent);color:var(--on);font-weight:800;margin-bottom:.9rem}
.steps p{margin-top:.4rem;font-size:.97rem}

.split{display:grid;gap:2.5rem;align-items:center}
@media(min-width:56rem){.split{grid-template-columns:1fr .8fr;gap:3.5rem}}
.split dl{margin:1.6rem 0 0}
.split dt{font-weight:700;color:var(--ink);font-size:1.08rem;margin-top:1.2rem}
.split dt:first-child{margin-top:0}
.split dd{margin:.2rem 0 0;font-size:.97rem}

.next{background:var(--card);border:1px solid var(--line);border-radius:1.2rem;padding:1.6rem}
@media(min-width:48rem){.next{padding:2.2rem}}
.tag{display:inline-block;font-size:.8rem;font-weight:700;color:var(--accent);border:1px solid var(--accent);
     border-radius:999px;padding:.15rem .7rem;margin-bottom:.9rem}
.next p{margin-top:.8rem;max-width:44rem}

.offer{border:1px solid var(--accent);border-radius:1.2rem;padding:1.6rem;background:var(--card);
       display:grid;gap:1.5rem;align-items:center}
@media(min-width:56rem){.offer{grid-template-columns:1fr auto;gap:3rem;padding:2.4rem}}
.offer h2{max-width:none}
.offer ul{margin:1.1rem 0 0;padding:0;list-style:none;display:grid;gap:.45rem}
@media(min-width:40rem){.offer ul{grid-template-columns:1fr 1fr;column-gap:2rem}}
.offer li{padding-left:1.4rem;position:relative}
.offer li::before{content:"";position:absolute;left:0;top:.55em;width:.6rem;height:.6rem;border-radius:50%;
                  background:var(--accent)}
.offer small{display:block;margin-top:1rem;font-size:.9rem;color:var(--muted)}

.faq{max-width:46rem;margin-top:1.8rem}
.faq details{border-top:1px solid var(--line);padding:1rem 0}
.faq details:last-child{border-bottom:1px solid var(--line)}
.faq summary{cursor:pointer;font-weight:700;color:var(--ink);font-size:1.08rem;list-style:none;display:flex;
             justify-content:space-between;gap:1rem}
.faq summary::-webkit-details-marker{display:none}
.faq summary::after{content:"+";font-weight:500;font-size:1.4rem;line-height:1;color:var(--accent)}
.faq details[open] summary::after{content:"-"}
.faq p{margin-top:.6rem}

.last{padding-top:1rem;padding-bottom:4rem}
.last h2{margin-bottom:1.4rem}
footer{border-top:1px solid var(--line);padding:1.6rem 0 2.4rem;color:var(--muted);font-size:.9rem;
       display:flex;flex-wrap:wrap;gap:.6rem 1.5rem;justify-content:space-between}
footer a{color:var(--muted)}
footer a:hover{color:var(--ink)}
footer nav{display:flex;flex-wrap:wrap;gap:.4rem 1.1rem}
"""


def _css(accent: str) -> str:
    a, a2, on = ACCENTS.get(accent) or ACCENTS[ACCENT]
    return _CSS.replace("__ACCENT__", a).replace("__ACCENT2__", a2).replace("__ON__", on)


def _proof(postings_checked) -> str:
    """One true sentence about the latest search, or nothing."""
    try:
        n = int(postings_checked or 0)
    except (TypeError, ValueError):
        n = 0
    if n < 1000:
        return ""
    return ('<div class="proof"><div class="wrap"><p>Our latest search checked <b>{:,} job postings</b> '
            'on company career pages and job boards, and kept only the ones that matched.</p></div></div>'
            ).format(n)


def landing_html(public_url: str = "", postings_checked=None, accent: str = "") -> str:
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
  <meta name="color-scheme" content="dark"/>
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
  <link rel="icon" type="image/png" href="/app/icon-192.png"/>
  <style>{_css(accent)}</style>
</head>
<body>
<header class="wrap top">
  <a class="brand" href="/"><img src="/app/icon-192.png" alt="" width="35" height="35"/>Job Hunter</a>
  <div class="toplinks">
    <a class="btn quiet" href="/login">Sign in</a>
    <a class="btn go" href="/register">Start my search</a>
  </div>
</header>

<main>
  <div class="wrap hero">
    <div>
      <h1>Meet your personal AI headhunter agent. It works for you, not for the companies.</h1>
      <p class="lead">We search company career pages and job boards for tech roles in Israel every day, shortlist
      the ones that fit your CV, and get each application ready for you.</p>
      <div class="ctas">
        <a class="btn go big" href="/register">Start my search</a>
        <a class="btn line big" href="#how">See how it works</a>
      </div>
      <p class="offer-line"><b>Premium is free for a limited time.</b> Sign up with Google or email. No card.</p>
    </div>
    <figure class="phone">
      <img src="/landing-assets/shortlist.webp" width="390" height="844"
           alt="The Job Hunter app showing a matched role, its match score and the reasons it fits the CV"/>
      <figcaption>The app, with a demo profile and example companies</figcaption>
    </figure>
  </div>

  {_proof(postings_checked)}

  <section class="sec">
    <div class="wrap">
      <h2>Job boards work for employers. We work for you.</h2>
      <p class="intro">A job board is paid to fill a company's opening. Your agent has one client: you. It reads
      everything, brings you the few roles that fit, and does the paperwork.</p>
      <div class="vs">
        <div class="them">
          <h3>Searching on your own</h3>
          <ul>
            <li>The same boards every evening, and the same reposted listings</li>
            <li>No way to tell which roles are worth an application</li>
            <li>One CV sent everywhere</li>
            <li>The same form typed again for every company</li>
          </ul>
        </div>
        <div class="us">
          <h3>With your personal AI headhunter agent</h3>
          <ul>
            <li>A fresh search every day, including companies' own career pages</li>
            <li>Each role scored against your CV, with the reasons written out</li>
            <li>A CV and cover letter written for each job you approve</li>
            <li>Your answers saved once and filled in for you</li>
          </ul>
        </div>
      </div>
    </div>
  </section>

  <section class="sec tight" id="how">
    <div class="wrap">
      <h2>From your CV to a ready application</h2>
      <p class="intro">Setup takes less than five minutes. After that your personal agent runs on its own, and
      nothing goes out without your approval.</p>
      <ol class="steps">
        <li><h3>Hand over your CV</h3>
            <p>We read your CV and fill in the titles, skills and locations to look for. Change anything you like.</p></li>
        <li><h3>Your agent searches every day</h3>
            <p>Company career pages and job boards, on a schedule you choose. We drop dead links and duplicates.</p></li>
        <li><h3>You approve or pass</h3>
            <p>Swipe through your shortlist. Teach your agent why you passed, and tomorrow's list gets closer to
            what you want.</p></li>
      </ol>
    </div>
  </section>

  <section class="sec tight">
    <div class="wrap split">
      <div>
        <h2>What we prepare for every role you approve</h2>
        <dl>
          <dt>A CV written for that job</dt>
          <dd>Reordered and reworded around what the role asks for, using only what is already in your CV. We check
          every line against it.</dd>
          <dt>A cover letter</dt>
          <dd>Written from your full CV and the job description. Edit it or send it as it is.</dd>
          <dt>Your answers, filled in</dt>
          <dd>Notice period, salary expectations and the other questions forms keep asking. You answer once.</dd>
        </dl>
      </div>
      <figure class="phone">
        <img src="/landing-assets/dashboard.webp" width="390" height="844" loading="lazy"
             alt="The Job Hunter dashboard showing approved roles in the queue and applications sent"/>
        <figcaption>Your queue: approved roles, ready to go</figcaption>
      </figure>
    </div>
  </section>

  <section class="sec tight">
    <div class="wrap">
      <div class="next">
        <span class="tag">Rolling out</span>
        <h2>Next: your agent applies for you</h2>
        <p>On the company application systems we support, your agent can fill in and send the application for the
        roles you approved. We are switching this on account by account. Until it reaches yours, every application
        is prepared and one tap away.</p>
      </div>
    </div>
  </section>

  <section class="sec tight">
    <div class="wrap">
      <div class="offer">
        <div>
          <h2>Premium is free for a limited time</h2>
          <ul>
            <li>A new search every day</li>
            <li>Every match from each search</li>
            <li>Tailored CVs and cover letters every month</li>
            <li>Alerts by push, email, Telegram or WhatsApp</li>
          </ul>
          <small>No card needed. You can export or delete your data whenever you want.</small>
        </div>
        <a class="btn go big" href="/register">Start my search</a>
      </div>
    </div>
  </section>

  <section class="sec tight">
    <div class="wrap">
      <h2>Questions people ask first</h2>
      <div class="faq">
        <details><summary>Which jobs do you cover?</summary>
          <p>Tech-industry roles in Israel: product, marketing, sales, customer success, data, design, QA and
          engineering. We read company career pages and job boards.</p></details>
        <details><summary>Do you apply to anything without asking me?</summary>
          <p>No. We shortlist, you decide. Your agent works on your behalf, but it never makes a decision for
          you.</p></details>
        <details><summary>What happens to my CV?</summary>
          <p>We use it to score jobs against your experience and to write your tailored CV and cover letters. An
          employer sees it only when you apply. You can export or delete everything from Settings.</p></details>
        <details><summary>Will the tailored CV invent experience I do not have?</summary>
          <p>No. We reorder and reword what is in your CV. Anything the job asks for that your CV does not show is
          listed as a gap, and you can edit the result before it is used.</p></details>
        <details><summary>How is this different from job alerts?</summary>
          <p>Alerts send you every listing that contains a keyword. Your agent reads each posting against your CV,
          explains the fit, learns from what you pass on, and prepares the application.</p></details>
        <details><summary>Do I need to install anything?</summary>
          <p>No. It runs in your browser on phone and computer. You can add it to your home screen and switch on
          notifications.</p></details>
        <details><summary>What does it cost?</summary>
          <p>Premium is free for a limited time and needs no card. If that changes, we will tell you in advance.</p></details>
      </div>
    </div>
  </section>

  <section class="last">
    <div class="wrap">
      <h2>Let your agent read the listings. You pick the ones worth your time.</h2>
      <a class="btn go big" href="/register">Start my search</a>
    </div>
  </section>
</main>

<div class="wrap">
  <footer>
    <span>&copy; 2026 Job Hunter</span>
    <nav aria-label="Footer">
      <a href="/terms">Terms</a><a href="/privacy">Privacy</a><a href="/login">Sign in</a>
      <a href="/register">Create account</a><a href="mailto:support@job-hunter.work">support@job-hunter.work</a>
    </nav>
  </footer>
</div>
</body>
</html>"""

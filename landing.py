"""
landing.py - the public page at the bare domain, for signed-out visitors.

Rebuilt 2026-11-11 for job seekers (the first version, Step 10, read like a
product sheet). Positioning: it works like a private headhunter - for you, not
for the companies. The offer is "Premium is free for a limited time"; the word
"beta" does not appear on the page.

Self-contained like /login: no external stylesheet, no script, and the one
headline typeface (Bricolage Grotesque, SIL OFL - assets/landing/) is inlined.

Every claim on this page has to be true of the product today:
- the search covers tech roles in Israel;
- sending the application for you is described as being switched on gradually
  (the engine runs for admins only);
- the activity line is drawn from the latest search's own numbers and is left
  out when there are none (see landing_html's `postings_checked`).
"""
import base64
import html
import os

_MARK_SVG = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" '
             'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
             '<rect x="2" y="7" width="20" height="14" rx="2"/>'
             '<path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/></svg>')

TITLE = "Job Hunter - your own headhunter, working for you"
DESCRIPTION = ("Job Hunter works like a private headhunter for tech roles in Israel: it searches company "
               "career pages and job boards every day, shortlists the roles that fit your CV, and gets "
               "each application ready.")

_FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "assets", "landing", "bricolage-grotesque-latin-wght.woff2")


def _font_face() -> str:
    """The headline face as a data: URL. Missing file = the fallback stack."""
    try:
        with open(_FONT_PATH, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("ascii")
    except OSError:
        return ""
    return ("@font-face{font-family:'Bricolage Grotesque';font-style:normal;font-weight:200 800;"
            "font-display:swap;src:url(data:font/woff2;base64,%s) format('woff2')}" % b64)


_CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{--paper:#f4f5fa;--white:#fff;--ink:#141b34;--body:#39415a;--muted:#5b6479;--line:#d9dcea;
      --indigo:#4338ca;--indigo2:#3730a3;--marker:#ffe566;--green:#0f7b5a;
      --display:'Bricolage Grotesque','Avenir Next','Segoe UI',system-ui,sans-serif;
      --text:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',sans-serif}
html{scroll-behavior:smooth}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
body{margin:0;color:var(--body);background:var(--paper);font-family:var(--text);font-size:1.02rem;line-height:1.6}
a{color:var(--indigo)}
a:focus-visible,summary:focus-visible{outline:3px solid var(--indigo);outline-offset:3px;border-radius:.3rem}
.wrap{max-width:70rem;margin:0 auto;padding:0 1.15rem}
@media(min-width:48rem){.wrap{padding:0 2rem}}
h1,h2,h3{font-family:var(--display);color:var(--ink);margin:0}
h1{font-size:clamp(2.35rem,6.4vw,4.3rem);line-height:1.02;letter-spacing:-.025em;font-weight:750}
h2{font-size:clamp(1.7rem,4vw,2.6rem);line-height:1.08;letter-spacing:-.02em;font-weight:700;max-width:22ch}
h3{font-size:1.14rem;line-height:1.25;font-weight:650}
p{margin:0}
.mk{background:var(--marker);padding:.05em .2em;border-radius:.2em;color:var(--ink);-webkit-box-decoration-break:clone;box-decoration-break:clone}

.top{display:flex;align-items:center;justify-content:space-between;gap:.6rem;padding-top:1.1rem;padding-bottom:1.1rem}
.brand{display:flex;align-items:center;gap:.6rem;color:var(--ink);text-decoration:none;
       font-family:var(--display);font-weight:700;font-size:1.15rem;white-space:nowrap}
.mark{width:2.1rem;height:2.1rem;border-radius:.6rem;display:flex;align-items:center;justify-content:center;
      background:var(--ink);color:var(--marker)}
.mark svg{width:1.1rem;height:1.1rem}
.toplinks{display:flex;align-items:center;gap:.2rem}
@media(max-width:26rem){.toplinks .btn{padding:.5rem .7rem;font-size:.84rem}.brand{font-size:1.02rem;gap:.45rem}.mark{width:1.9rem;height:1.9rem}}
.btn{display:inline-flex;align-items:center;justify-content:center;padding:.7rem 1.15rem;border-radius:999px;
     font-weight:650;font-size:.95rem;text-decoration:none;border:2px solid transparent;white-space:nowrap}
.btn.go{color:#fff;background:var(--indigo)}
.btn.go:hover{background:var(--indigo2)}
.btn.quiet{color:var(--ink)}
.btn.quiet:hover{border-color:var(--line)}
.btn.line{color:var(--ink);border-color:var(--ink)}
.btn.line:hover{background:var(--ink);color:#fff}
.btn.big{padding:.95rem 1.6rem;font-size:1.05rem}
.btn.dark{color:var(--ink);background:var(--white);border-color:var(--ink)}
.btn.dark:hover{background:var(--ink);color:#fff}

.hero{display:grid;gap:2.5rem;padding-top:2rem;padding-bottom:3.5rem;align-items:center}
@media(min-width:62rem){.hero{grid-template-columns:1.05fr .95fr;gap:3.5rem;padding-top:3.5rem;padding-bottom:5rem}}
.hero .lead{font-size:1.18rem;line-height:1.5;color:var(--body);max-width:33rem;margin:1.4rem 0 1.8rem}
.ctas{display:flex;flex-wrap:wrap;gap:.7rem;align-items:center}
.offer-line{margin-top:1.1rem;font-size:.95rem;color:var(--muted)}
.offer-line b{color:var(--ink)}

.note{background:var(--white);border:2px solid var(--ink);border-radius:1.1rem;padding:1.2rem 1.2rem 1.3rem;
      box-shadow:.55rem .55rem 0 var(--ink)}
@media(min-width:62rem){.note{transform:rotate(-1.1deg)}}
.note-head{font-family:var(--display);font-weight:700;color:var(--ink);font-size:1.3rem;line-height:1.15}
.note-sub{font-size:.9rem;color:var(--muted);margin:.25rem 0 1rem}
.role{border-top:1px solid var(--line);padding:.9rem 0}
.role:last-of-type{padding-bottom:.2rem}
.role-top{display:flex;justify-content:space-between;gap:.8rem;align-items:baseline}
.role-t{font-weight:700;color:var(--ink);font-size:1.02rem}
.role-c{font-size:.88rem;color:var(--muted)}
.fit{font-family:var(--display);font-weight:750;color:var(--green);font-size:1.25rem;white-space:nowrap}
.why{font-size:.92rem;margin-top:.45rem;color:var(--body)}
.acts{display:grid;grid-template-columns:1fr 1fr 1.3fr;gap:.45rem;margin-top:.8rem}
.acts span{text-align:center;font-size:.86rem;font-weight:650;padding:.5rem;border-radius:999px;
           border:1.5px solid var(--line);color:var(--muted)}
.acts span.yes{color:#fff;background:var(--indigo);border-color:var(--indigo)}

.proof{background:var(--ink);color:#dfe3f3}
.proof p{font-family:var(--display);font-weight:600;font-size:clamp(1.2rem,2.6vw,1.65rem);line-height:1.3;
         padding:1.6rem 0;max-width:46rem}
.proof b{color:var(--marker);font-weight:750}

.sec{padding:3.6rem 0}
@media(min-width:48rem){.sec{padding:5rem 0}}
.sec .intro{margin:.9rem 0 2.2rem;max-width:36rem;color:var(--body)}

.vs{display:grid;gap:1rem}
@media(min-width:48rem){.vs{grid-template-columns:1fr 1fr;gap:1.4rem}}
.vs > div{border-radius:1rem;padding:1.4rem}
.vs .them{background:#e9ebf3}
.vs .us{background:var(--white);border:2px solid var(--ink)}
.vs h3{margin-bottom:.7rem}
.vs ul{margin:0;padding:0;list-style:none}
.vs li{padding:.55rem 0;border-top:1px solid var(--line)}
.vs li:first-child{border-top:0;padding-top:0}

.steps{list-style:none;margin:0;padding:0;display:grid;gap:1.6rem;counter-reset:s}
@media(min-width:48rem){.steps{grid-template-columns:1fr 1fr;gap:2rem 3rem}}
@media(min-width:68rem){.steps{grid-template-columns:repeat(4,1fr);gap:2rem}}
.steps li{counter-increment:s;border-top:2px solid var(--ink);padding-top:.9rem}
.steps li::before{content:counter(s);font-family:var(--display);font-weight:750;font-size:2.4rem;line-height:1;
                  color:var(--ink);display:block;margin-bottom:.5rem}
.steps p{margin-top:.35rem;font-size:.97rem}

.kit{display:grid;gap:2rem;align-items:start}
@media(min-width:56rem){.kit{grid-template-columns:.9fr 1.1fr;gap:3.5rem}}
.kit dl{margin:0}
.kit dt{font-family:var(--display);font-weight:650;color:var(--ink);font-size:1.1rem;margin-top:1.2rem}
.kit dt:first-child{margin-top:0}
.kit dd{margin:.2rem 0 0;font-size:.97rem}
.cv{background:var(--white);border:1px solid var(--line);border-radius:.9rem;padding:1.3rem}
.cv-name{font-family:var(--display);font-weight:700;color:var(--ink);font-size:1.2rem}
.cv-for{font-size:.88rem;color:var(--muted);margin:.1rem 0 1rem}
.cv h4{font-size:.9rem;font-weight:700;color:var(--ink);margin:1rem 0 .3rem}
.cv ul{margin:0;padding-left:1.1rem;font-size:.93rem}
.cv li{margin:.3rem 0}
.cv .gap{color:var(--muted)}
.cv-foot{margin-top:1rem;padding-top:.8rem;border-top:1px solid var(--line);font-size:.86rem;color:var(--muted)}

.offer{background:var(--marker);color:var(--ink)}
.offer .in{display:grid;gap:1.6rem;padding:3rem 0;align-items:center}
@media(min-width:56rem){.offer .in{grid-template-columns:1fr auto;gap:3rem;padding:4rem 0}}
.offer h2{max-width:none}
.offer ul{margin:1rem 0 0;padding:0;list-style:none;display:grid;gap:.4rem;font-weight:550}
@media(min-width:40rem){.offer ul{grid-template-columns:1fr 1fr;column-gap:2rem}}
.offer li::before{content:"+ ";font-weight:800}
.offer small{display:block;margin-top:.9rem;font-size:.9rem}

.faq{max-width:46rem}
.faq details{border-top:1px solid var(--line);padding:1rem 0}
.faq details:last-child{border-bottom:1px solid var(--line)}
.faq summary{cursor:pointer;font-family:var(--display);font-weight:650;color:var(--ink);font-size:1.12rem;
             list-style:none;display:flex;justify-content:space-between;gap:1rem}
.faq summary::-webkit-details-marker{display:none}
.faq summary::after{content:"+";font-weight:500;font-size:1.4rem;line-height:1}
.faq details[open] summary::after{content:"-"}
.faq p{margin-top:.6rem}

.last{text-align:left;padding:3.6rem 0 4rem}
.last h2{margin-bottom:1.4rem}
footer{border-top:1px solid var(--line);padding:1.6rem 0 2.4rem;color:var(--muted);font-size:.9rem;
       display:flex;flex-wrap:wrap;gap:.6rem 1.5rem;justify-content:space-between}
footer a{color:var(--muted)}
footer a:hover{color:var(--ink)}
footer nav{display:flex;flex-wrap:wrap;gap:.4rem 1.1rem}
"""


def _proof(postings_checked) -> str:
    """One true sentence about the latest search, or nothing."""
    try:
        n = int(postings_checked or 0)
    except (TypeError, ValueError):
        n = 0
    if n < 1000:
        return ""
    return ('<div class="proof"><div class="wrap"><p>The latest search checked <b>{:,} job postings</b> '
            'on company career pages and job boards, and kept only the ones that matched.</p></div></div>'
            ).format(n)


def landing_html(public_url: str = "", postings_checked=None) -> str:
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
  <meta name="theme-color" content="#f4f5fa"/>
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
  <style>{_font_face()}{_CSS}</style>
</head>
<body>
<header class="wrap top">
  <a class="brand" href="/"><span class="mark">{_MARK_SVG}</span>Job Hunter</a>
  <div class="toplinks">
    <a class="btn quiet" href="/login">Sign in</a>
    <a class="btn go" href="/register">Start my search</a>
  </div>
</header>

<main>
  <div class="wrap hero">
    <div>
      <h1>Your own headhunter. Working for you, not for the companies.</h1>
      <p class="lead">Job Hunter searches company career pages and job boards for tech roles in Israel every day,
      shortlists the ones that fit your CV, and gets each application ready for you.</p>
      <div class="ctas">
        <a class="btn go big" href="/register">Start my search</a>
        <a class="btn line big" href="#how">See how it works</a>
      </div>
      <p class="offer-line"><b>Premium is free for a limited time.</b> Sign up with Google or email. No card.</p>
    </div>

    <div class="note" aria-label="Example of a daily shortlist">
      <div class="note-head">3 roles worth your time today</div>
      <div class="note-sub">Example shortlist. Yours is built from your own CV.</div>
      <div class="role">
        <div class="role-top">
          <div><div class="role-t">Senior Product Manager</div>
          <div class="role-c">Northwind Labs, Tel Aviv, hybrid</div></div>
          <div class="fit">92%</div>
        </div>
        <p class="why">They ask for 5+ years in B2C product. <span class="mk">You have 6, leading two teams.</span></p>
        <div class="acts"><span>Pass</span><span>Later</span><span class="yes">Approve</span></div>
      </div>
      <div class="role">
        <div class="role-top">
          <div><div class="role-t">Customer Success Lead</div>
          <div class="role-c">Harbor Analytics, Herzliya</div></div>
          <div class="fit">87%</div>
        </div>
        <p class="why"><span class="mk">Enterprise onboarding is in your last two roles.</span> Posted on their own careers page yesterday.</p>
      </div>
      <div class="role">
        <div class="role-top">
          <div><div class="role-t">Product Analyst</div>
          <div class="role-c">Juniper Pay, Ramat Gan, hybrid</div></div>
          <div class="fit">81%</div>
        </div>
        <p class="why">SQL and experimentation match. <span class="mk">No fintech background, which they prefer.</span></p>
      </div>
    </div>
  </div>

  {_proof(postings_checked)}

  <section class="sec">
    <div class="wrap">
      <h2>Job boards work for employers. This works for you.</h2>
      <p class="intro">A job board is paid to fill a company's opening. A headhunter who worked only for you would
      read everything, bring you the few roles that fit, and do the paperwork. That is the job Job Hunter does.</p>
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
          <h3>With Job Hunter</h3>
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

  <section class="sec" id="how" style="padding-top:0">
    <div class="wrap">
      <h2>From your CV to a ready application</h2>
      <p class="intro">Setup takes about five minutes. After that it runs on its own, and nothing goes out without your yes.</p>
      <ol class="steps">
        <li><h3>Hand over your CV</h3>
            <p>It reads your CV and fills in the titles, skills and locations to look for. Change anything you like.</p></li>
        <li><h3>It searches every day</h3>
            <p>Company career pages and job boards, on a schedule you choose. Dead links and duplicates are dropped.</p></li>
        <li><h3>You approve or pass</h3>
            <p>Swipe through your shortlist. Tell it why you passed and tomorrow's list gets closer to what you want.</p></li>
        <li><h3>The application is ready</h3>
            <p>For every role you approve: a tailored CV, a cover letter, your answers, and a link to the employer's own page.</p></li>
      </ol>
    </div>
  </section>

  <section class="sec" style="padding-top:0">
    <div class="wrap kit">
      <div>
        <h2>What arrives with every role you approve</h2>
        <dl style="margin-top:1.6rem">
          <dt>A CV written for that job</dt>
          <dd>Reordered and reworded around what the role asks for, using only what is already in your CV. Every line is checked against it.</dd>
          <dt>A cover letter</dt>
          <dd>Written from your full CV and the job description. Edit it or send it as it is.</dd>
          <dt>Your answers, filled in</dt>
          <dd>Notice period, salary expectations and the other questions forms keep asking. You answer once.</dd>
          <dt>The employer's own page</dt>
          <dd>Wherever it can, the link goes to the company's application page and not to a repost.</dd>
          <dt>Sending it for you</dt>
          <dd>On application systems it supports, Job Hunter can fill in and send the form itself. This is being switched on gradually, and it only ever sends roles you approved.</dd>
        </dl>
      </div>
      <div class="cv" aria-label="Example of a tailored CV summary">
        <div class="cv-name">How your CV meets this job</div>
        <div class="cv-for">Senior Product Manager at Northwind Labs</div>
        <h4>What they ask for, and where your CV shows it</h4>
        <ul>
          <li>5+ years in B2C product: <span class="mk">6 years, two consumer apps</span></li>
          <li>Experimentation at scale: <span class="mk">ran the A/B programme for onboarding</span></li>
          <li>Leading a team: <span class="mk">managed 4 product managers</span></li>
          <li class="gap">Marketplace experience: not in your CV, shown as a gap and not invented</li>
        </ul>
        <h4>What changed in this version</h4>
        <ul>
          <li>Summary rewritten around consumer growth</li>
          <li>Experimentation moved to the top of your current role</li>
        </ul>
        <div class="cv-foot">Example. You choose which CV is sent, and you can edit it first.</div>
      </div>
    </div>
  </section>

  <section class="offer">
    <div class="wrap in">
      <div>
        <h2>Premium is free for a limited time</h2>
        <ul>
          <li>A new search every day</li>
          <li>Your best matches from each search</li>
          <li>Tailored CVs and cover letters every month</li>
          <li>Alerts by push, email, Telegram or WhatsApp</li>
        </ul>
        <small>No card needed. You can export or delete your data whenever you want.</small>
      </div>
      <a class="btn dark big" href="/register">Start my search</a>
    </div>
  </section>

  <section class="sec">
    <div class="wrap">
      <h2>Questions people ask first</h2>
      <div class="faq" style="margin-top:1.8rem">
        <details><summary>Which jobs does it cover?</summary>
          <p>Tech-industry roles in Israel: product, marketing, sales, customer success, data, design, QA and
          engineering. It reads company career pages and job boards. If you are searching outside Israel, it is not
          the right tool yet.</p></details>
        <details><summary>Does it apply to anything without asking me?</summary>
          <p>No. It shortlists, you decide. Only roles you approve are prepared, and nothing is sent in your name
          unless you turned that on yourself.</p></details>
        <details><summary>What happens to my CV?</summary>
          <p>It is used to score jobs against your experience and to write your tailored CV and cover letters. An
          employer sees it only when you apply. You can export or delete everything from Settings. The details are
          in the <a href="/privacy">Privacy Policy</a>.</p></details>
        <details><summary>Will the tailored CV invent experience I do not have?</summary>
          <p>No. It can reorder and reword what is in your CV. Anything the job asks for that your CV does not show
          is listed as a gap, and you can edit the result before it is used.</p></details>
        <details><summary>How is this different from job alerts?</summary>
          <p>Alerts send you every listing that contains a keyword. Job Hunter reads each posting against your CV,
          explains the fit, learns from what you pass on, and prepares the application.</p></details>
        <details><summary>Do I need to install anything?</summary>
          <p>No. It runs in your browser on phone and computer. You can add it to your home screen and switch on
          notifications if you want them.</p></details>
        <details><summary>What does it cost?</summary>
          <p>Premium is free for a limited time and needs no card. If that changes you will be told in advance.</p></details>
      </div>
    </div>
  </section>

  <section class="last">
    <div class="wrap">
      <h2>Let it read the listings. You pick the ones worth your time.</h2>
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

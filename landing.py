"""
landing.py - the public page at the bare domain, for signed-out visitors.

Rebuilt 2026-11-12 around Noa, the product's persona (the owner's decision):
"Meet Noa, your personal AI headhunter agent. She works for you, not for the
companies." Noa is an AI agent and the page says so (FAQ "Who is Noa?").

Design (the owner's picks): dark only, deep forest green with butter yellow,
Fraunces for headings and Inter for text (both SIL OFL, served from
assets/landing/), Noa speaking in chat bubbles. The app's logo keeps its own
colours. Product pictures are screenshots of /app run against a demo account
with invented companies - no real person's data.

Three Noa illustrations (a, d, e) are kept for an A/B test. Until the
analytics PR brings consent and measurement, every visitor sees the first one;
`?noa=d` / `?noa=e` show the others (for reviewing).

Every claim on this page has to be true of the product today:
- the search covers tech roles in Israel;
- applying for you is described as rolling out (no promise);
- the activity number is the latest search's own count, left out when there
  is none (see `postings_checked`);
- the word "beta" never appears: the offer is "Premium is free for a limited
  time" (accounts are on the Beta plan internally).

Text stays ASCII-safe for app.send_html, which strips U+0080-U+00FF: use
entities (&copy;) rather than those characters.
"""
import html

NOA_VARIANTS = ("a", "d", "e")
ASSETS = {                       # every file /landing-assets/ will serve, and its type
    "job-card.webp": "image/webp",
    "analytics.webp": "image/webp",
    "job-details.webp": "image/webp",
    "noa-a.webp": "image/webp",
    "noa-d.webp": "image/webp",
    "noa-e.webp": "image/webp",
    "fraunces-latin-wght.woff2": "font/woff2",
    "inter-latin-wght.woff2": "font/woff2",
}

TITLE = "Job Hunter - meet Noa, your personal AI headhunter agent"
DESCRIPTION = ("Noa is your personal AI headhunter agent for tech roles in Israel. She works for you, not for "
               "the companies: she reads every opening, brings you only the ones that fit your CV, and gets "
               "each application ready.")

_CSS = """
@font-face{font-family:Fraunces;font-style:normal;font-weight:100 900;font-display:swap;
  src:url(/landing-assets/fraunces-latin-wght.woff2) format('woff2')}
@font-face{font-family:Inter;font-style:normal;font-weight:100 900;font-display:swap;
  src:url(/landing-assets/inter-latin-wght.woff2) format('woff2')}
*,*::before,*::after{box-sizing:border-box}
:root{color-scheme:dark;--bg:#0c1f17;--bg2:#0f251c;--panel:#12291f;--line:#2c4a3c;--text:#c4d4c6;--muted:#8ba593;
      --strong:#fbf6e6;--accent:#ffd766;--accent2:#ffe28f;--on:#1f1a05;
      --display:Fraunces,Georgia,'Times New Roman',serif;
      --body:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
html{scroll-behavior:smooth}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--body);font-size:1.0625rem;line-height:1.6}
a{color:var(--accent)}
a:focus-visible,summary:focus-visible{outline:3px solid var(--accent);outline-offset:3px;border-radius:.4rem}
img{max-width:100%;height:auto;display:block}
.wrap{max-width:76rem;margin:0 auto;padding-left:1.15rem;padding-right:1.15rem}
@media(min-width:48rem){.wrap{padding-left:2.25rem;padding-right:2.25rem}}
h1,h2,h3{font-family:var(--display);color:var(--strong);margin:0;font-weight:600}
h1{font-size:clamp(2.6rem,6.6vw,4.6rem);line-height:1.0;letter-spacing:-.02em}
h2{font-size:clamp(1.9rem,4vw,2.9rem);line-height:1.08;letter-spacing:-.015em;max-width:22ch}
h3{font-size:1.4rem;line-height:1.2}
p{margin:0}

.top{display:flex;align-items:center;justify-content:space-between;gap:.75rem;padding-top:1.2rem;padding-bottom:1.2rem}
.brand{display:flex;align-items:center;gap:.65rem;color:var(--strong);text-decoration:none;font-weight:700;font-size:1.12rem;white-space:nowrap}
.brand img{width:2.2rem;height:2.2rem;border-radius:.6rem}
.nav{display:flex;align-items:center;gap:.35rem}
.btn{display:inline-flex;align-items:center;justify-content:center;padding:.75rem 1.25rem;border-radius:.85rem;
     font-weight:650;font-size:1rem;text-decoration:none;border:1.5px solid transparent;white-space:nowrap}
.btn.go{background:var(--accent);color:var(--on)}
.btn.go:hover{background:var(--accent2)}
.btn.ghost{color:var(--strong)}
.btn.ghost:hover{border-color:var(--line)}
.btn.line{color:var(--strong);border-color:var(--line)}
.btn.line:hover{border-color:var(--strong)}
.btn.big{padding:1rem 1.7rem;font-size:1.1rem}
@media(max-width:26rem){.nav .btn{padding:.55rem .8rem;font-size:.9rem}.brand{font-size:1rem}.brand img{width:1.9rem;height:1.9rem}}

.hero{display:grid;gap:2.75rem;padding-top:2rem;padding-bottom:4rem;align-items:start}
@media(min-width:62rem){.hero{grid-template-columns:1fr 1fr;gap:3.5rem;padding-top:3rem;padding-bottom:5.5rem}}
.lead{font-size:1.25rem;line-height:1.5;margin:1.6rem 0 2rem;max-width:33rem}
.ctas{display:flex;flex-wrap:wrap;gap:.75rem}
.offer-line{margin-top:1.15rem;font-size:.95rem;color:var(--muted)}
.offer-line b{color:var(--strong)}

.chat{display:grid;gap:.9rem}
.msg{display:flex;gap:.75rem;align-items:flex-start}
.msg img{width:2.9rem;height:2.9rem;border-radius:50%;flex:none;border:2px solid var(--accent)}
.bub{background:var(--panel);border:1px solid var(--line);border-radius:.4rem 1.3rem 1.3rem 1.3rem;padding:.95rem 1.15rem;
     color:var(--strong);font-size:1.08rem;line-height:1.45}
.bub small{display:block;color:var(--muted);font-size:.8rem;margin-bottom:.25rem}
.shot{border-radius:1rem;overflow:hidden;border:1px solid var(--line);background:#111827}
.chat .shot{margin-left:3.65rem}
@media(max-width:30rem){.chat .shot{margin-left:0}}

.sec{padding-top:4rem;padding-bottom:4rem;border-top:1px solid var(--line)}
@media(min-width:48rem){.sec{padding-top:5.5rem;padding-bottom:5.5rem}}
.intro{margin:1rem 0 2.4rem;max-width:38rem;font-size:1.1rem}

.vs{display:grid;gap:1rem}
@media(min-width:48rem){.vs{grid-template-columns:1fr 1fr;gap:1.4rem}}
.vs > div{border-radius:1.1rem;padding:1.5rem 1.6rem}
.vs .them{background:var(--bg2);border:1px solid var(--line)}
.vs .us{background:var(--panel);border:1.5px solid var(--accent)}
.vs h3{margin-bottom:.8rem;font-size:1.3rem}
.vs ul{margin:0;padding:0;list-style:none}
.vs li{padding:.6rem 0;border-top:1px solid var(--line)}
.vs li:first-child{border-top:0;padding-top:0}
.vs .them li{color:var(--muted)}
.vs .us li{color:var(--strong)}

.steps{list-style:none;margin:0;padding:0;display:grid;gap:2rem;counter-reset:s}
@media(min-width:56rem){.steps{grid-template-columns:repeat(3,1fr);gap:2.4rem}}
.steps li{counter-increment:s}
.steps li::before{content:counter(s);font-family:var(--display);font-weight:600;font-size:2.8rem;line-height:1;color:var(--accent);
                  display:block;margin-bottom:.6rem}
.steps p{margin-top:.45rem}

.split{display:grid;gap:2.4rem;align-items:start}
@media(min-width:60rem){.split{grid-template-columns:.85fr 1.15fr;gap:3.5rem}}
.split dl{margin:1.8rem 0 0}
.split dt{font-family:var(--display);font-weight:600;color:var(--strong);font-size:1.25rem;margin-top:1.4rem}
.split dt:first-child{margin-top:0}
.split dd{margin:.3rem 0 0}

.soon{display:grid;gap:1.4rem;align-items:center;background:var(--panel);border:1px solid var(--line);border-radius:1.2rem;
      padding:1.6rem 1.7rem;margin-top:3rem}
@media(min-width:48rem){.soon{grid-template-columns:auto 1fr;gap:2rem}}
.tag{display:inline-block;font-size:.85rem;font-weight:650;color:var(--on);background:var(--accent);border-radius:999px;
     padding:.25rem .8rem;justify-self:start}
.soon h3{margin-bottom:.4rem}

.offer{background:var(--panel);border:1.5px solid var(--accent);border-radius:1.4rem;padding:2rem;display:grid;gap:1.8rem;align-items:center}
@media(min-width:56rem){.offer{grid-template-columns:1fr auto;gap:3rem;padding:2.8rem 3rem}}
.offer h2{max-width:none}
.offer ul{margin:1.2rem 0 0;padding:0 0 0 1.2rem;display:grid;gap:.35rem;color:var(--strong)}
@media(min-width:40rem){.offer ul{grid-template-columns:1fr 1fr;column-gap:2.4rem}}
.offer small{display:block;margin-top:1rem;font-size:.92rem;color:var(--muted)}

.faq{max-width:48rem;margin-top:2rem}
.faq details{border-top:1px solid var(--line);padding:1.1rem 0}
.faq details:last-child{border-bottom:1px solid var(--line)}
.faq summary{cursor:pointer;font-family:var(--display);font-weight:600;color:var(--strong);font-size:1.25rem;
             list-style:none;display:flex;justify-content:space-between;gap:1rem}
.faq summary::-webkit-details-marker{display:none}
.faq summary::after{content:"+";font-family:var(--body);font-weight:400;font-size:1.5rem;line-height:1;color:var(--accent)}
.faq details[open] summary::after{content:"\\2212"}
.faq p{margin-top:.65rem;max-width:42rem}

.last{display:grid;gap:1.6rem;align-items:center}
@media(min-width:56rem){.last{grid-template-columns:auto 1fr;gap:2.4rem}}
.last img{width:6.5rem;height:6.5rem;border-radius:50%;border:3px solid var(--accent)}
.last h2{max-width:24ch;margin-bottom:1.3rem}

footer{border-top:1px solid var(--line);padding-top:1.7rem;padding-bottom:2.5rem;color:var(--muted);font-size:.92rem;
       display:flex;flex-wrap:wrap;gap:.6rem 1.6rem;justify-content:space-between}
footer a{color:var(--muted)}
footer a:hover{color:var(--strong)}
footer nav{display:flex;flex-wrap:wrap;gap:.4rem 1.2rem}
"""


def _count(postings_checked) -> int:
    try:
        n = int(postings_checked or 0)
    except (TypeError, ValueError):
        return 0
    return n if n >= 1000 else 0


def _morning_message(postings_checked) -> str:
    """Noa's second message. The number is the latest search's real count."""
    n = _count(postings_checked)
    read = ("I read {:,} postings in my latest search, and kept only the ones worth your time.".format(n)
            if n else "I read every new tech posting in Israel for you, and keep only the ones worth your time.")
    return read + " This one is very you. And one I'd skip, honestly. You can do better."


def landing_html(public_url: str = "", postings_checked=None, noa: str = "a") -> str:
    noa = noa if noa in NOA_VARIANTS else NOA_VARIANTS[0]
    face = "/landing-assets/noa-%s.webp" % noa
    url = html.escape((public_url or "").rstrip("/") + "/", quote=True)
    t = html.escape(TITLE, quote=True)
    d = html.escape(DESCRIPTION, quote=True)
    canon = f'<link rel="canonical" href="{url}"/>' if public_url else ""
    og_url = f'<meta property="og:url" content="{url}"/>' if public_url else ""
    msg2 = html.escape(_morning_message(postings_checked))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover"/>
  <meta name="color-scheme" content="dark"/>
  <meta name="theme-color" content="#0c1f17"/>
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
  <link rel="preload" href="/landing-assets/fraunces-latin-wght.woff2" as="font" type="font/woff2" crossorigin/>
  <style>{_CSS}</style>
</head>
<body data-noa="{noa}">
<header class="wrap top">
  <a class="brand" href="/"><img src="/app/icon-192.png" alt="" width="36" height="36"/>Job Hunter</a>
  <nav class="nav" aria-label="Account">
    <a class="btn ghost" href="/login">Sign in</a>
    <a class="btn go" href="/register">Meet Noa</a>
  </nav>
</header>

<main>
  <section class="wrap hero">
    <div>
      <h1>Meet Noa, your personal AI headhunter agent.</h1>
      <p class="lead">She works for you, not for the companies. Noa reads every tech opening in Israel, brings you only
      the ones that fit your CV, and gets your application ready.</p>
      <div class="ctas">
        <a class="btn go big" href="/register">Meet Noa</a>
        <a class="btn line big" href="#how">See how she works</a>
      </div>
      <p class="offer-line"><b>Premium is free for a limited time.</b> Sign up with Google or email. No card.</p>
    </div>

    <div class="chat" aria-label="Noa introduces herself">
      <div class="msg"><img src="{face}" alt="Noa" width="46" height="46"/>
        <p class="bub"><small>Noa</small>Hi, I'm Noa. Send me your CV and I'll get to know you. Then I do the searching.</p></div>
      <div class="msg"><img src="{face}" alt="" width="46" height="46"/>
        <p class="bub"><small>Noa, every morning</small>{msg2}</p></div>
      <div class="shot"><img src="/landing-assets/job-card.webp" width="1400" height="986"
        alt="A job card in Job Hunter: Senior Product Manager, Growth at Northwind Labs, 92% match, with the reasons it fits and Pass, Approve and Decide later buttons"/></div>
    </div>
  </section>

  <section class="sec">
    <div class="wrap">
      <h2>Job boards work for employers. Noa works for you.</h2>
      <p class="intro">A job board is paid to fill a company's opening. Noa isn't. She reads everything, brings you the few
      roles that fit, and does the paperwork, the way a headhunter would if she worked only for you.</p>
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
          <h3>With Noa</h3>
          <ul>
            <li>Noa searches every day, including companies' own career pages</li>
            <li>Every role is scored against your CV, and Noa tells you why it fits</li>
            <li>Noa writes a CV and cover letter for each role you approve</li>
            <li>You answer the usual form questions once. Noa remembers them</li>
          </ul>
        </div>
      </div>
    </div>
  </section>

  <section class="sec" id="how">
    <div class="wrap">
      <h2>How Noa works</h2>
      <p class="intro">Setup is less than five minutes. After that Noa runs on her own, and nothing goes out without your approval.</p>
      <ol class="steps">
        <li><h3>Tell Noa who you are</h3>
            <p>Upload your CV and Noa fills in the titles, skills and locations to look for. Change anything you like.</p></li>
        <li><h3>Noa searches every day</h3>
            <p>Company career pages and job boards, on a schedule you choose. Dead links and duplicates never reach you.</p></li>
        <li><h3>You approve, Noa prepares</h3>
            <p>Swipe through your shortlist and teach Noa why you passed. Tomorrow's list gets closer to you.</p></li>
      </ol>
    </div>
  </section>

  <section class="sec">
    <div class="wrap split">
      <div>
        <h2>What Noa prepares for every role you approve</h2>
        <dl>
          <dt>A CV written for that job</dt>
          <dd>Noa reorders and rewords your CV around what the role asks for, using only what is already in it. Anything she
          can't back up is shown as a gap, never invented.</dd>
          <dt>A cover letter</dt>
          <dd>Written from your full CV and the job description. Edit it or send it as it is.</dd>
          <dt>Your answers, filled in</dt>
          <dd>Notice period, salary expectations and the other questions forms keep asking. You answer once.</dd>
        </dl>
      </div>
      <div>
        <div class="shot"><img src="/landing-assets/job-details.webp" width="1400" height="746" loading="lazy"
          alt="A role's details in Job Hunter: a check that the posting is still open, a cover letter to generate, and a button to tailor your CV for this job"/></div>
      </div>
    </div>
    <div class="wrap">
      <div class="soon">
        <span class="tag">Rolling out</span>
        <div>
          <h3>Noa can apply for you, too</h3>
          <p>On the application systems she supports, Noa can fill in and send the application herself, only for roles you
          approved. It is being switched on gradually, account by account.</p>
        </div>
      </div>
    </div>
  </section>

  <section class="sec">
    <div class="wrap split">
      <div>
        <h2>You always know where you stand</h2>
        <p class="intro">Noa keeps score honestly: how many roles you approved, how many you really applied to, and what she
        learned from the ones you passed.</p>
      </div>
      <div>
        <div class="shot"><img src="/landing-assets/analytics.webp" width="1400" height="380" loading="lazy"
          alt="Job Hunter statistics: roles in the queue, applied, deferred and passed, with approval and applied rates"/></div>
      </div>
    </div>
  </section>

  <section class="sec">
    <div class="wrap">
      <div class="offer">
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
        <a class="btn go big" href="/register">Meet Noa</a>
      </div>
    </div>
  </section>

  <section class="sec">
    <div class="wrap">
      <h2>Questions people ask Noa first</h2>
      <div class="faq">
        <details><summary>Who is Noa?</summary>
          <p>Noa is Job Hunter's AI agent. She is software, not a person, and she works only for you: no company pays to
          appear in your list.</p></details>
        <details><summary>Which jobs does Noa cover?</summary>
          <p>Tech-industry roles in Israel: product, marketing, sales, customer success, data, design, QA and engineering.
          She reads company career pages and job boards.</p></details>
        <details><summary>Does Noa apply to anything without asking me?</summary>
          <p>No. Noa shortlists, you decide. She works on your behalf, but she never makes a decision for you.</p></details>
        <details><summary>What happens to my CV?</summary>
          <p>Noa uses it to score jobs against your experience and to write your tailored CV and cover letters. An employer
          sees it only when you apply. You can export or delete everything from Settings.</p></details>
        <details><summary>Will Noa invent experience I don't have?</summary>
          <p>No. She reorders and rewords what is in your CV. Anything the job asks for that your CV doesn't show is listed as
          a gap, and you can edit the result before it's used.</p></details>
        <details><summary>How is this different from job alerts?</summary>
          <p>Alerts send you every listing with a keyword. Noa reads each posting against your CV, explains the fit, learns
          from what you pass on, and prepares the application.</p></details>
        <details><summary>Do I need to install anything?</summary>
          <p>No. Noa works in your browser on phone and computer. You can add Job Hunter to your home screen and turn on
          notifications.</p></details>
        <details><summary>What does it cost?</summary>
          <p>Premium is free for a limited time and needs no card. If that changes, you'll be told in advance.</p></details>
      </div>
    </div>
  </section>

  <section class="sec">
    <div class="wrap last">
      <img src="{face}" alt="" width="104" height="104" loading="lazy"/>
      <div>
        <h2>Let Noa read the listings. You pick the ones that deserve you.</h2>
        <a class="btn go big" href="/register">Meet Noa</a>
      </div>
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

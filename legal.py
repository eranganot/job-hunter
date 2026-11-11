"""
legal.py - the Terms of Service (/terms) and Privacy Policy (/privacy).

Step 6 of EXECUTION_PLAN_NEXT.md, published 2026-11-11 with the owner's
answers: operator a private individual in Ramat Gan, Israel; Israeli law and
the courts of Tel Aviv; minimum age 18; EU users in scope; database in Railway
EU West (Amsterdam); Sentry in its EU region; server logs kept up to 12 months;
liability capped at 12 months' fees or ILS 100; contact support@job-hunter.work;
written for a free service, paid plans to come with their own terms.

Drafts, not legal advice. Every statement about what the app does has to stay
true of the code: when a processor, a cookie or a retention period changes,
change this file in the same PR (tests/test_legal_pages.py pins the facts that
can drift: the processors, the cookies, the retention numbers).

Served through send_html, which strips U+0080-U+00FF: write such characters as
HTML entities (the test fails on a raw one).
"""
import html

OPERATOR = "Eran Ganot"
OPERATOR_PLACE = "Ramat Gan, Israel"
CONTACT = "support@job-hunter.work"
EFFECTIVE = "11 November 2026"

_MARK_SVG = ('<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" '
             'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
             '<rect x="2" y="7" width="20" height="14" rx="2"/>'
             '<path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/></svg>')

_CSS = """
*,*::before,*::after{box-sizing:border-box}
body{margin:0;color:#e5e7eb;background:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;line-height:1.65}
a{color:#a5b4fc}
.shell{max-width:46rem;margin:0 auto;padding:0 1rem 3rem}
@media(min-width:40rem){.shell{padding:0 1.5rem 4rem}}
nav{display:flex;align-items:center;justify-content:space-between;padding:1.1rem 0;margin-bottom:1rem}
.brand{display:flex;align-items:center;gap:.6rem;color:#fff;text-decoration:none;font-weight:700}
.mark{width:2rem;height:2rem;border-radius:.6rem;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#6366f1,#4338ca)}
.mark svg{width:1rem;height:1rem}
.navlinks a{color:#cbd5e1;text-decoration:none;font-size:.9rem;margin-left:1rem}
.navlinks a:hover{color:#fff}
h1{color:#fff;font-size:clamp(1.7rem,4.5vw,2.2rem);line-height:1.2;margin:.5rem 0 .25rem}
.meta{color:#9ca3af;font-size:.9rem;margin:0 0 1.5rem}
.summary{background:rgba(99,102,241,.1);border:1px solid rgba(99,102,241,.35);border-radius:1rem;padding:1rem 1.2rem;margin:0 0 2rem}
.summary p{margin:.2rem 0}
.summary ul{margin:.4rem 0 0;padding-left:1.2rem}
h2{color:#fff;font-size:1.2rem;margin:2.2rem 0 .5rem;padding-top:1.2rem;border-top:1px solid #1f2937}
h3{color:#e5e7eb;font-size:1rem;margin:1.2rem 0 .3rem}
p,li{color:#cbd5e1}
ul{padding-left:1.2rem}
li{margin:.25rem 0}
.tbl{width:100%;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.9rem;margin:.6rem 0}
th,td{text-align:left;vertical-align:top;padding:.55rem .6rem;border-bottom:1px solid #1f2937}
th{color:#fff;font-weight:600;background:#111827}
td{color:#cbd5e1}
footer{border-top:1px solid #1f2937;margin-top:3rem;padding-top:1.25rem;color:#9ca3af;font-size:.85rem;display:flex;flex-wrap:wrap;gap:.5rem 1.25rem;justify-content:space-between}
footer a{color:#9ca3af}
"""


def _page(title: str, description: str, body: str, public_url: str = "", path: str = "") -> str:
    t = html.escape(title + " - Job Hunter", quote=True)
    d = html.escape(description, quote=True)
    canon = ""
    if public_url:
        canon = '<link rel="canonical" href="%s"/>' % html.escape(public_url.rstrip("/") + path, quote=True)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover"/>
  <meta name="theme-color" content="#0f172a"/>
  <title>{t}</title>
  <meta name="description" content="{d}"/>
  {canon}
  <style>{_CSS}</style>
</head>
<body>
<div class="shell">
  <nav>
    <a class="brand" href="/"><span class="mark">{_MARK_SVG}</span>Job Hunter</a>
    <span class="navlinks"><a href="/terms">Terms</a><a href="/privacy">Privacy</a><a href="/login">Sign in</a></span>
  </nav>
  {body}
  <footer>
    <span>&copy; 2026 Job Hunter</span>
    <span><a href="/terms">Terms of Service</a>&nbsp;&middot; <a href="/privacy">Privacy Policy</a>&nbsp;&middot; <a href="mailto:{CONTACT}">{CONTACT}</a></span>
  </footer>
</div>
</body>
</html>"""


# --------------------------------------------------------------------------
# Terms of Service
# --------------------------------------------------------------------------

_TERMS = f"""
<h1>Terms of Service</h1>
<p class="meta">Effective {EFFECTIVE}</p>

<div class="summary">
  <p><b>The short version</b> (the full terms below are what count):</p>
  <ul>
    <li>Job Hunter is run by one person, {OPERATOR}, in {OPERATOR_PLACE}. It is in beta and free.</li>
    <li>You must be 18 or older. You are responsible for what you upload and for every application sent in your name.</li>
    <li>The app finds and scores jobs and prepares applications. Scores are suggestions; it does not promise you interviews or a job.</li>
    <li>You can export your data or delete your account at any time from Settings.</li>
  </ul>
</div>

<h2>1. Who we are and what these terms cover</h2>
<p>Job Hunter (the "Service", at job-hunter.work) is operated by {OPERATOR}, a private individual based in {OPERATOR_PLACE} ("we", "us"). These Terms of Service are an agreement between you and us about your use of the Service. Our <a href="/privacy">Privacy Policy</a> explains how we handle your personal data and is part of these terms.</p>
<p>By creating an account or using the Service you agree to these terms. If you do not agree, do not use the Service.</p>

<h2>2. Who can use it</h2>
<ul>
  <li>You must be at least 18 years old.</li>
  <li>You must give accurate information when you sign up and keep your contact details current, because job applications go out under them.</li>
  <li>One person per account. Keep your password secret; you are responsible for activity under your account. Tell us at <a href="mailto:{CONTACT}">{CONTACT}</a> if you think someone else has used it.</li>
</ul>

<h2>3. What the Service does</h2>
<p>Job Hunter searches public job boards and companies' career pages for roles that fit the preferences you set, scores each role against your CV and explains the score, and helps you apply: it can prepare a CV tailored to a job, a cover letter and answers to application questions.</p>
<p>Some features use automated tools, including large language models, to read job descriptions and your CV and to write text. That text can be wrong or incomplete. <b>Read anything the Service prepares before it is sent.</b></p>
<h3>Applications sent for you</h3>
<p>Where you switch it on and the employer's application system allows it, the Service can fill in and submit an application for you. When it does, it sends the employer the details from your profile, your CV (or the tailored CV you chose) and the answers you saved. You authorise each such submission by turning the feature on and approving the job, and you are responsible for the accuracy of what is sent. The Service shows you the result of each attempt; it cannot always confirm that an employer received an application, and it says so when it cannot. Employers decide what to do with applications; we are not a party to any hiring decision.</p>
<p>The feature is in testing and may not be available to every account.</p>

<h2>4. Your content</h2>
<p>You keep ownership of everything you upload or write, such as your CV, profile and answers ("Your Content"). You give us a limited permission to store, process and transmit Your Content only as needed to run the Service for you: for example, to score jobs against it, to tailor it, and to send it to an employer when you apply. This permission ends when you delete the content or your account, except for copies an employer already received.</p>
<p>You confirm that Your Content is yours to use, is accurate, and does not break any law or anyone else's rights.</p>

<h2>5. Acceptable use</h2>
<p>You agree not to:</p>
<ul>
  <li>submit false or misleading applications, or apply in someone else's name;</li>
  <li>use the Service to send spam or to flood employers with applications for roles you do not want;</li>
  <li>try to access other users' data, probe or break the Service's security, or get around its limits;</li>
  <li>scrape, copy or resell the Service or the job data it collects, or use it to build a competing product;</li>
  <li>use the Service in a way that breaks the law or the terms of the job sites and employers it connects to.</li>
</ul>
<p>We may suspend or close an account that breaks these rules, and we will tell you why unless the law or safety prevents it.</p>

<h2>6. Third-party sites</h2>
<p>Job listings, career pages and application systems belong to other companies. We do not control them, do not guarantee that listings are accurate, current or still open, and are not responsible for those sites or for employers' conduct. Your use of them is under their own terms.</p>

<h2>7. Price and paid plans</h2>
<p>During the beta the Service is free. Usage limits apply to each plan and are shown in the app. If we introduce paid plans, they will come with their own pricing and terms, which we will show you before you are charged anything. Nothing becomes paid without your explicit agreement.</p>

<h2>8. Availability and changes</h2>
<p>The Service is in beta. Features may change, be limited or be removed, and the Service may be unavailable at times. We may change or end the Service; if we end it, we will give you reasonable notice where we can so that you can export your data.</p>

<h2>9. Ending your account</h2>
<p>You can delete your account at any time from Settings &rarr; Profile &rarr; Your data. Deletion takes effect after a 14-day grace period, during which signing in cancels it; after that your data is removed as described in the Privacy Policy. You can export your data first from the same place.</p>
<p>We may close your account if you seriously or repeatedly break these terms, or if we end the Service.</p>

<h2>10. Our intellectual property</h2>
<p>The Service, its software and its design belong to us and are protected by law. These terms give you the right to use the Service as offered, not any other right in it.</p>

<h2>11. No guarantees</h2>
<p>The Service is provided "as is" and "as available". To the extent the law allows, we make no promises that it will be uninterrupted or error-free, that match scores or prepared content are accurate, or that using it will lead to interviews or employment.</p>

<h2>12. Limitation of liability</h2>
<p>To the extent the law allows:</p>
<ul>
  <li>we are not liable for indirect or consequential losses, such as lost opportunities, lost earnings or lost data, arising from your use of the Service;</li>
  <li>our total liability to you for all claims relating to the Service is limited to the greater of the amount you paid us in the 12 months before the claim arose, or ILS 100.</li>
</ul>
<p>Nothing in these terms limits liability that cannot be limited by law, or your rights as a consumer under the law that applies to you.</p>

<h2>13. Changes to these terms</h2>
<p>We may update these terms. If a change is significant, we will tell you by email or in the app at least 14 days before it takes effect. If you keep using the Service after that, the new terms apply; if you do not agree, you can delete your account.</p>

<h2>14. Law and disputes</h2>
<p>These terms are governed by the laws of the State of Israel. The competent courts of Tel Aviv-Yafo have exclusive jurisdiction, except that if you are a consumer living in the EU or another country whose law gives you the right to bring a claim in your own courts, you keep that right.</p>

<h2>15. Contact</h2>
<p>{OPERATOR}, {OPERATOR_PLACE}. Email: <a href="mailto:{CONTACT}">{CONTACT}</a>.</p>
"""


# --------------------------------------------------------------------------
# Privacy Policy
# --------------------------------------------------------------------------

_PRIVACY = f"""
<h1>Privacy Policy</h1>
<p class="meta">Effective {EFFECTIVE}</p>

<div class="summary">
  <p><b>The short version:</b></p>
  <ul>
    <li>We collect what you give us (account, CV, preferences, answers) and what the app produces for you (matched jobs, scores, applications).</li>
    <li>We use it only to run Job Hunter for you. We do not sell it, show you ads or use advertising trackers.</li>
    <li>It is stored in the EU (Amsterdam). A few service providers, listed below, process parts of it for us.</li>
    <li>You can download all of it or delete your account at any time in Settings.</li>
  </ul>
</div>

<h2>1. Who is responsible</h2>
<p>The controller of your personal data is {OPERATOR}, a private individual based in {OPERATOR_PLACE}, who operates Job Hunter ("we", "us"). Contact: <a href="mailto:{CONTACT}">{CONTACT}</a>. This policy applies to job-hunter.work and the Job Hunter app.</p>

<h2>2. What we collect</h2>
<h3>You give us</h3>
<ul>
  <li><b>Account:</b> name, email address, and a password (stored only as a salted hash) - or, if you sign in with Google, your Google account's name, email address and account identifier.</li>
  <li><b>Your CV</b> and the text we read from it.</li>
  <li><b>Profile and preferences:</b> job titles, keywords, locations, salary expectations, work setup, notice period, sponsorship needs, links (such as LinkedIn), phone number, and the answers you save for application questions.</li>
  <li><b>Your decisions:</b> the jobs you approve, pass (with the reason, if you give one) or save for later.</li>
  <li><b>Notification settings:</b> the channels you choose. If you connect Telegram or WhatsApp (through your own Twilio account), we store the tokens you enter, encrypted.</li>
  <li><b>Messages to support</b> sent from the app or by email.</li>
</ul>
<h3>The Service creates</h3>
<ul>
  <li>Jobs found for you, their match scores and explanations, tailored CVs, cover letters, and the status and result of each application.</li>
  <li>A record of activity in your account (searches, applications, settings changes) and usage counts used to apply plan limits.</li>
  <li>Technical data: sign-in sessions, IP address and browser details in server logs, and error reports when something breaks.</li>
</ul>
<p>We do not ask for, and ask you not to include in your CV or answers, sensitive information such as health, religion or political views unless you choose to share it with employers.</p>

<h2>3. How we use it, and on what basis</h2>
<div class="tbl"><table>
  <tr><th>Purpose</th><th>Legal basis (GDPR)</th></tr>
  <tr><td>Running your account and the features you use: searching, scoring, tailoring, preparing and - when you turn it on - submitting applications; notifications you asked for</td><td>Performance of our contract with you</td></tr>
  <tr><td>Sending service emails: confirming your address, password resets, changes to these policies</td><td>Contract; legitimate interest</td></tr>
  <tr><td>Security, preventing abuse, applying usage limits, fixing errors</td><td>Legitimate interest in a secure, working service</td></tr>
  <tr><td>Improving the Service using aggregated statistics (for example, how many sign-ups reach a first search, how often pass reasons recur)</td><td>Legitimate interest</td></tr>
  <tr><td>Meeting legal obligations and handling legal claims</td><td>Legal obligation; legitimate interest</td></tr>
</table></div>
<p>We do not sell your data, use it for advertising, or make decisions about you that have legal or similarly significant effects without a human: the Service ranks jobs for you, but you decide what to apply for.</p>

<h2>4. Who we share it with</h2>
<h3>Employers, when you apply</h3>
<p>When you apply to a job through the Service, we send that employer (through its application system, such as Greenhouse, Lever, Comeet, Ashby, SmartRecruiters or the company's own careers site) the details the form asks for: your name, contact details, CV, cover letter and answers. The employer then handles them under its own privacy policy.</p>
<h3>Service providers</h3>
<p>These providers process data on our behalf, only to provide their service to us:</p>
<div class="tbl"><table>
  <tr><th>Provider</th><th>What for</th><th>Data involved</th></tr>
  <tr><td>Railway</td><td>Hosting the app and its database (EU West, Amsterdam)</td><td>All data stored by the Service; server logs</td></tr>
  <tr><td>Google (Gemini API)</td><td>Reading CVs and job descriptions, scoring, writing tailored CVs, cover letters and answers</td><td>CV text, profile and preferences, job descriptions</td></tr>
  <tr><td>Google (Sign-in)</td><td>Signing in with Google, if you choose it</td><td>Name, email, Google account identifier</td></tr>
  <tr><td>Resend</td><td>Sending email (confirmation, password reset, notifications)</td><td>Email address, message content</td></tr>
  <tr><td>Cloudflare</td><td>The job-hunter.work domain, network delivery and forwarding mail sent to our support address</td><td>IP address and request data; support emails</td></tr>
  <tr><td>Google (Gmail)</td><td>The mailbox that receives support messages</td><td>Your support messages and email address</td></tr>
  <tr><td>Sentry (EU region)</td><td>Error reports when something breaks</td><td>Technical details of the error, account identifier; stack-frame variables are not sent</td></tr>
  <tr><td>Your browser's push service (Google, Apple, Mozilla, Microsoft)</td><td>Push notifications, if you turn them on</td><td>A device push address, notification text</td></tr>
  <tr><td>Telegram; Twilio (WhatsApp)</td><td>Notifications, only if you connect them with your own bot or account</td><td>Notification text, your chat or phone number</td></tr>
</table></div>
<p>To find jobs, the Service also queries job boards and job-data providers. Those queries contain search terms such as job titles and locations, not your name, contact details or CV.</p>
<p>When we introduce paid plans, a payment provider will process payment details; we will update this policy before that happens.</p>
<h3>Otherwise</h3>
<p>We disclose data if the law requires it, to protect the rights and safety of users or others, or as part of a transfer of the Service to a new operator, who would be bound by this policy.</p>

<h2>5. International transfers</h2>
<p>Our database is in the EU, and we are based in Israel, which the European Commission recognises as providing adequate protection for personal data. Some providers above (for example Google, Resend and Twilio) may process data in the United States or other countries. Where they do, they rely on safeguards recognised under EU law, such as the EU-US Data Privacy Framework or the European Commission's Standard Contractual Clauses.</p>

<h2>6. How long we keep it</h2>
<div class="tbl"><table>
  <tr><th>Data</th><th>Kept</th></tr>
  <tr><td>Your account, CV, profile, jobs, applications and activity</td><td>Until you delete your account. Older history beyond your plan's window is hidden, not deleted.</td></tr>
  <tr><td>After you ask to delete your account</td><td>14-day grace period (signing in cancels it), then removed from the database. Anonymous usage counts (with your account identifier removed) may remain.</td></tr>
  <tr><td>Sign-in sessions</td><td>14 days from the last use</td></tr>
  <tr><td>Email confirmation and password-reset links</td><td>48 hours and 1 hour; each works once</td></tr>
  <tr><td>Usage counts of AI features</td><td>90 days</td></tr>
  <tr><td>Server logs</td><td>Up to 12 months</td></tr>
  <tr><td>Error reports</td><td>Up to 90 days</td></tr>
  <tr><td>Database backups</td><td>Overwritten on the backup provider's rolling schedule</td></tr>
  <tr><td>Support messages</td><td>As long as needed to help you, and no longer than 12 months after the conversation ends</td></tr>
</table></div>
<p>Applications already sent to employers are kept by them under their own policies; deleting your Job Hunter account does not withdraw them.</p>

<h2>7. Your rights</h2>
<p>Depending on where you live (including under the EU GDPR and Israel's Protection of Privacy Law), you can:</p>
<ul>
  <li><b>access</b> your data and <b>take it with you</b> - Settings &rarr; Profile &rarr; Your data &rarr; Export downloads everything in your account and your CV;</li>
  <li><b>correct</b> it - most of it is editable in the app;</li>
  <li><b>delete</b> it - Settings &rarr; Profile &rarr; Your data &rarr; Delete my account;</li>
  <li><b>object to</b> or ask us to <b>restrict</b> processing based on legitimate interest;</li>
  <li><b>withdraw consent</b> for optional features (such as notifications) by turning them off.</li>
</ul>
<p>For anything you cannot do in the app, email <a href="mailto:{CONTACT}">{CONTACT}</a>. We answer within 30 days. You can also complain to a data protection authority: in Israel, the Privacy Protection Authority; in the EU, the authority in your country.</p>

<h2>8. Cookies and local storage</h2>
<p>We use only what the Service needs to work. No advertising or analytics cookies.</p>
<div class="tbl"><table>
  <tr><th>Name</th><th>What it does</th><th>Lasts</th></tr>
  <tr><td><code>session</code></td><td>Keeps you signed in</td><td>Signs you out after 14 days without use (the cookie itself expires after 30)</td></tr>
  <tr><td><code>g_state</code>, <code>g_verifier</code></td><td>Protect the Google sign-in step</td><td>10 minutes</td></tr>
  <tr><td>Local storage: <code>jh_notify_nudge</code>, <code>jh_vapid_key</code></td><td>Remember whether we asked about notifications, and the push setup</td><td>Until you clear them</td></tr>
  <tr><td>Session storage: <code>jh.nav</code></td><td>Remembers which screen and tab you were on, so a refresh keeps your place</td><td>Until you close the app</td></tr>
  <tr><td>App cache (service worker)</td><td>Lets the app load quickly and install on your device</td><td>Until the next app version</td></tr>
</table></div>
<p>Because these are strictly necessary, we do not ask for consent to them.</p>

<h2>9. Security</h2>
<p>We protect your data with encrypted connections (HTTPS), hashed passwords, encrypted storage of notification tokens, limits on sign-in and other attempts, and access to production data limited to the operator. No system is perfectly secure; if a breach affects your data, we will tell you and the relevant authorities as the law requires.</p>

<h2>10. Children</h2>
<p>The Service is for people aged 18 and over. We do not knowingly collect data from anyone younger; if we learn we have, we delete it.</p>

<h2>11. Changes to this policy</h2>
<p>If we make a significant change, we will tell you by email or in the app before it takes effect. The date at the top shows the current version.</p>

<h2>12. Contact</h2>
<p>{OPERATOR}, {OPERATOR_PLACE}. Email: <a href="mailto:{CONTACT}">{CONTACT}</a>.</p>
"""


def terms_html(public_url: str = "") -> str:
    return _page("Terms of Service",
                 "The terms for using Job Hunter.", _TERMS, public_url, "/terms")


def privacy_html(public_url: str = "") -> str:
    return _page("Privacy Policy",
                 "What Job Hunter collects, why, who it shares it with, and your rights.",
                 _PRIVACY, public_url, "/privacy")

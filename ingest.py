# -*- coding: utf-8 -*-
"""
The Record . data collector
=================================================================
Where the data comes from, and how we collect it, WITHOUT touching paste.trade.

Counterparty publishes its own machine readable record. This script reads it and
builds `the_record_data.json`, which the site loads. Same public source paste.trade
uses (the show's own words), our own independent pipeline, a different output:
a written, taught, searchable archive instead of a live P&L terminal.

SOURCES (all public, all the show's own output)
  RSS feed         https://feeds.buzzsprout.com/2535072.rss
  per episode      .../<id>/transcript.json   (word level, timestamped)
                   .../<id>/chapters.json      (titled timestamps)
  jump to moment   https://www.buzzsprout.com/2535072/episodes/<id>?t=<seconds>
  prices (charts)  Stooq free CSV  (stocks)  /  CoinGecko free  (crypto)   [stubbed]

PIPELINE
  1. fetch RSS, find episodes (skip ones already in the cache)
  2. download transcript.json + chapters.json for each new episode
  3. extract calls  -> LLM pass if ANTHROPIC_API_KEY is set, else heuristic
  4. attach a price window per ticker for the Anatomy charts        [stub]
  5. draft a 3 minute Dispatch                                       [LLM optional]
  6. write the_record_data.json  (feed, tape, voices, dispatch)
  7. a human (the editor seat) reviews before publish

Run nightly:  python ingest.py            (Netlify scheduled fn / GitHub Action / cron)
Backfill all: python ingest.py --all
"""

import json, re, sys, os, time, urllib.request, urllib.error, xml.etree.ElementTree as ET
from datetime import datetime, timezone

SHOW_ID = "2535072"
RSS     = f"https://feeds.buzzsprout.com/{SHOW_ID}.rss"
EP_BASE = f"https://www.buzzsprout.com/{SHOW_ID}/episodes/"
OUT     = "the_record_data.json"
UA      = {"User-Agent": "the-record-ingest/1.0"}

# Map the names actually said on the show to tickers. Extend as the show evolves.
TICKERS = {
    "amc":"AMC","micron":"MU","mu":"MU","hyperliquid":"HYPE","hype":"HYPE",
    "zcash":"ZEC","zec":"ZEC","strategy":"MSTR","mstr":"MSTR","strc":"MSTR","saylor":"MSTR",
    "spacex":"SPCX","bitcoin":"BTC","btc":"BTC","ethereum":"ETH","eth":"ETH",
    "marvell":"MRVL","mrvl":"MRVL","robinhood":"HOOD","hood":"HOOD","stubhub":"STUB",
    "snapchat":"SNAP","snap":"SNAP","oil":"WTI","crude":"WTI","take two":"TTWO","gta":"TTWO",
    "nvidia":"NVDA","tesla":"TSLA","uber":"UBER","amazon":"AMZN","coreweave":"CRWV",
}
LONG_WORDS  = ("long","bought","buying","i'm in","i am in","accumulate","holding","hold","spot")
SHORT_WORDS = ("short","shorting","fade","fading","sell","selling","puts")

def get(url, asjson=False):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw) if asjson else raw

def mmss(seconds):
    s = int(seconds); return f"{s//60}:{s%60:02d}"

# ---------- 1+2 : episodes and their transcripts ----------
def episodes():
    xml = get(RSS)
    ns = {"it":"http://www.itunes.com/dtds/podcast-1.0.dtd"}
    root = ET.fromstring(xml)
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        pub   = (item.findtext("pubDate") or "").strip()
        desc  = (item.findtext("description") or "")
        enc   = item.find("enclosure")
        url   = enc.get("url") if enc is not None else ""
        m = re.search(r"/episodes/(\d+)", url)
        epid = m.group(1) if m else None
        if not epid:
            continue
        out.append({
            "id": epid, "title": title, "pubDate": pub,
            "desc": re.sub("<[^>]+>", "", desc).strip(),
            "transcript": f"https://www.buzzsprout.com/{SHOW_ID}/{epid}/transcript.json",
            "chapters":   f"https://www.buzzsprout.com/{SHOW_ID}/{epid}/chapters.json",
        })
    return out

def sentences(transcript_json):
    """Rebuild sentences from word-level segments, keep each sentence's start time."""
    segs = transcript_json.get("segments", [])
    out, buf, start = [], [], None
    for s in segs:
        w = s.get("body", "")
        if start is None:
            start = s.get("startTime", 0)
        buf.append(w)
        if re.search(r"[.!?]$", w):
            out.append((start, " ".join(buf).strip()))
            buf, start = [], None
    if buf:
        out.append((start or 0, " ".join(buf).strip()))
    return out

# ---------- 3 : extraction ----------
# Levels are ONLY ever taken from what was said on air. Keyword + a price in the
# same sentence that names the ticker. No keyword or no number means null, never a guess.
LEVEL_KEYS = {
  "entry":  ["in at","entry","got in","bought at","i'm in","im in","added at","long from","short from","filled at"],
  "stop":   ["stop","invalidat","stopped out","stop loss","cut it at","get out at"],
  "target": ["target","take profit","looking for","aiming for","sell at","exit at","tp at","price target"],
}
PRICE_RE = re.compile(r"\$?\s?(\d{1,5}(?:\.\d{1,2})?)")

def extract_levels(sents, name):
    """Return {entry,stop,target,stated} using ONLY numbers spoken next to a keyword
    in a sentence that mentions the ticker. Anything not said on air stays None."""
    out = {"entry": None, "stop": None, "target": None, "stated": False}
    for _t, sent in sents:
        low = sent.lower()
        if not re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", low):
            continue
        for kind, keys in LEVEL_KEYS.items():
            if out[kind] is not None:
                continue
            if any(k in low for k in keys):
                m = PRICE_RE.search(sent)
                if m:
                    try: out[kind] = float(m.group(1))
                    except ValueError: pass
    out["stated"] = any(out[k] is not None for k in ("entry", "stop", "target"))
    return out

def extract_calls_heuristic(sents):
    """No-LLM fallback: find ticker + direction in a sentence, keep it as a receipt."""
    sym2name = {sym: name for name, sym in TICKERS.items()}
    calls = []
    for t, sent in sents:
        low = " " + sent.lower() + " "
        tk = None
        for name, sym in TICKERS.items():
            if re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", low):
                tk = sym; break
        if not tk:
            continue
        d = "L" if any(k in low for k in LONG_WORDS) else ("S" if any(k in low for k in SHORT_WORDS) else None)
        if not d:
            continue
        calls.append({"tk": tk, "dir": d, "quote": sent[:180], "ts": mmss(t), "secs": int(t)})
    # de-dupe by ticker, keep first mention
    seen, uniq = set(), []
    for c in calls:
        if c["tk"] in seen: continue
        seen.add(c["tk"]); uniq.append(c)
    for c in uniq:
        c["levels"] = extract_levels(sents, sym2name.get(c["tk"], c["tk"].lower()))
    return uniq

def extract_calls_llm(title, desc, sents):
    """Higher quality: ask Claude to read the transcript and return strict JSON calls."""
    import anthropic  # pip install anthropic
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    text = "\n".join(f"[{mmss(t)}] {s}" for t, s in sents)[:120000]
    prompt = (
        "You are the desk editor for a finance show's written record. From the transcript, "
        "extract every explicit trade CALL the host or a guest makes. Return ONLY a JSON array. "
        "Each item: {\"tk\":ticker,\"dir\":\"L\"or\"S\",\"status\":\"win|loss|open|flat\","
        "\"pct\":\"\",\"thesis\":one sentence in plain English,\"quote\":the exact spoken line,"
        "\"ts\":\"mm:ss\"}. No commentary, no markdown.\n\n"
        f"TITLE: {title}\nNOTES: {desc}\n\nTRANSCRIPT:\n{text}"
    )
    msg = client.messages.create(model="claude-sonnet-4-6", max_tokens=2000,
                                 messages=[{"role":"user","content":prompt}])
    raw = "".join(b.text for b in msg.content if b.type=="text").strip()
    raw = re.sub(r"^```json|```$", "", raw).strip()
    return json.loads(raw)

def extract(title, desc, transcript_json):
    sents = sentences(transcript_json)
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return extract_calls_llm(title, desc, sents), sents
        except Exception as e:
            print(f"  ! LLM extract failed ({e}); using heuristic", file=sys.stderr)
    return extract_calls_heuristic(sents), sents

# ---------- 4 : prices for the Anatomy charts (real) ----------
# Stocks -> Stooq free CSV. Crypto -> CoinGecko free OHLC. Futures -> an ETF/symbol proxy.
# This is historical price context for the chart studies. It is NOT the live "since call"
# P&L tracker, which is paste.trade's job and which we deliberately do not rebuild.
CRYPTO = {"ZEC":"zcash","HYPE":"hyperliquid","BTC":"bitcoin","ETH":"ethereum","SOL":"solana"}
STOOQ_SYM = {"WTI":"cl.f","SPX":"^spx","SP500":"^spx"}  # non-.us symbols / proxies

def _stooq(ticker):
    sym = STOOQ_SYM.get(ticker, ticker.lower() + ".us")
    csv = get(f"https://stooq.com/q/d/l/?s={sym}&i=d")
    rows = [ln.split(",") for ln in csv.strip().splitlines()[1:]]
    out = []
    for r in rows:
        if len(r) >= 5:
            try: out.append([r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4])])
            except ValueError: pass
    return out  # [date,o,h,l,c]

def _coingecko(ticker, days=180):
    cid = CRYPTO[ticker]
    data = get(f"https://api.coingecko.com/api/v3/coins/{cid}/ohlc?vs_currency=usd&days={days}", asjson=True)
    # [[ms,o,h,l,c],...]
    return [[datetime.utcfromtimestamp(p[0]/1000).strftime("%Y-%m-%d"), p[1], p[2], p[3], p[4]] for p in data]

def price_window(ticker, days=120):
    """Return real OHLC as the site's chart shape [[i,o,h,l,c],...], last `days` candles."""
    try:
        raw = _coingecko(ticker) if ticker in CRYPTO else _stooq(ticker)
    except Exception as e:
        print(f"  ! price fetch failed for {ticker} ({e})", file=sys.stderr); return []
    raw = raw[-days:]
    return [[i, round(o,4), round(h,4), round(l,4), round(c,4)] for i,(d,o,h,l,c) in enumerate(raw)]

# ---------- second source : the YouTube channel ----------
# @notthreadguy posts every stream to YouTube too. YouTube gives a channel RSS and timed
# captions, so it's the same pipeline, second source, with video deep links (watch?v=ID&t=Ns).
YT_HANDLE = "notthreadguy"

def yt_channel_id(handle=YT_HANDLE):
    html = get(f"https://www.youtube.com/@{handle}/videos")
    m = re.search(r'"channelId":"(UC[\w-]{22})"', html) or re.search(r'channel/(UC[\w-]{22})', html)
    return m.group(1) if m else None

def yt_videos(channel_id):
    xml = get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
    out = []
    for m in re.finditer(r"<entry>.*?<yt:videoId>([\w-]+)</yt:videoId>.*?<title>(.*?)</title>.*?<published>(.*?)</published>", xml, re.S):
        out.append({"id": m.group(1), "title": m.group(2), "published": m.group(3)})
    return out

def yt_transcript(video_id):
    """Timed captions via youtube-transcript-api (pip install youtube-transcript-api)."""
    from youtube_transcript_api import YouTubeTranscriptApi
    items = YouTubeTranscriptApi.get_transcript(video_id)  # [{text,start,duration}]
    # rebuild into sentence tuples (start_seconds, text)
    return [(int(it["start"]), it["text"]) for it in items]

def yt_uploads(handle=YT_HANDLE, since="20260101"):
    """All channel uploads since `since` (YYYYMMDD), newest first, via yt-dlp.
    The RSS feed only exposes ~15 recent videos; yt-dlp reaches the full back catalogue.
    Returns None on failure so the caller can fall back to the RSS path."""
    import subprocess
    url = f"https://www.youtube.com/@{handle}/videos"
    try:
        out = subprocess.run(
            ["yt-dlp", "--ignore-errors", "--dateafter", since,
             "--print", "%(id)s|%(upload_date)s|%(title)s", url],
            capture_output=True, text=True, timeout=900).stdout
    except Exception as e:
        print(f"  ! yt-dlp unavailable ({e}); using RSS fallback", file=sys.stderr); return None
    vids = []
    for ln in out.strip().splitlines():
        p = ln.split("|", 2)
        if len(p) >= 2 and p[0]:
            d = p[1]
            vids.append({"id": p[0],
                         "published": f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) >= 8 else "",
                         "title": p[2] if len(p) > 2 else ""})
    return vids

def youtube_calls(limit=10, since=None):
    """Timestamped lines from the YouTube captions -> tape (search) backfill.
    Uses the full uploads list (yt-dlp) when `since` is set, else the RSS feed."""
    vids = yt_uploads(since=since.replace("-", "")) if since else None
    if vids is None:
        cid = yt_channel_id()
        if not cid:
            print("  ! could not resolve YouTube channel id", file=sys.stderr); return []
        vids = yt_videos(cid)[:limit]
    else:
        print(f"  youtube uploads since {since}: {len(vids)} videos")
    rows = []
    for v in vids:
        pub = (v.get("published") or "")[:10]
        if since and pub and pub < since:
            continue
        try:
            sents = yt_transcript(v["id"])
        except Exception as e:
            print(f"  ! no captions for {v['id']} ({e})", file=sys.stderr); continue
        for c in extract_calls_heuristic(sents):
            rows.append([c["tk"], c["quote"], c["ts"], "yt:" + v["id"], c["secs"], pub])
    return rows  # deep link: https://www.youtube.com/watch?v=ID&t=NNNs

# ---------- Lexicon : the desk's vocabulary, taught from its own words ----------
LEXICON_SYS = (
 "You build a glossary for newcomers from a finance livestream's own words. "
 "Find the recurring slang, trader jargon, and signature phrases the desk actually uses "
 "(e.g. fullport, fade the war, never diversify, rebuy, roundtrip, exit liquidity). "
 "Return ONLY a JSON array, no prose. Each item: "
 '{"term": short phrase, "def": one plain-English sentence a beginner understands, '
 '"src": a short real example of how it is used, <=80 chars}. '
 "Aim for 10 to 16 of the most useful, genuinely recurring terms. No generic finance 101."
)

def build_lexicon(tape):
    """LLM pass over the spoken corpus -> [[term, def, src], ...]. None to keep baked."""
    if not ANTHROPIC_KEY or not tape:
        return None
    corpus = "\n".join(t[1] for t in tape if t[1])[:14000]
    try:
        raw = _anthropic(corpus, LEXICON_SYS, 2200)
        raw = raw[raw.find("["): raw.rfind("]") + 1]
        out = [[str(x.get("term","")).strip(), str(x.get("def","")).strip(), str(x.get("src","")).strip()]
               for x in json.loads(raw) if x.get("term")]
        return out or None
    except Exception as e:
        print(f"  ! lexicon pass failed ({e})", file=sys.stderr); return None

# ---------- 6 : assemble ----------
# ====================  STAGE 2 : CLASSIFY (LLM)  ====================
# Turn raw transcript into real calls. Throws out mentions, keeps direction +
# conviction + the verbatim line. Needs ANTHROPIC_API_KEY; falls back to heuristic.
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
CLASSIFY_MODEL = "claude-sonnet-4-6"
CLASSIFY_SYS = (
 "You extract trading CALLS from a finance livestream transcript. "
 "A call is an explicit directional view or a stated position on a tradeable asset "
 "(stock, crypto, commodity, index). NOT a call: casual mentions, questions, news "
 "recaps, jokes, or naming an asset with no view. Return ONLY a JSON array, no prose. "
 'Each item: {"ticker":"AMC","direction":"long"|"short","conviction":"high"|"medium"|"low",'
 '"quote": verbatim sentence up to 160 chars,"secs": integer start seconds}. '
 "Use standard tickers (AMC, MU, MSTR, BTC, ETH, ZEC, HYPE, NVDA, TSLA, HOOD, SPCX, CRWV). "
 "If there are no real calls, return []."
)

def _anthropic(content, system, max_tokens=1500):
    body = json.dumps({"model": CLASSIFY_MODEL, "max_tokens": max_tokens,
                       "system": system, "messages": [{"role":"user","content":content}]}).encode()
    last = ""
    for attempt in range(5):
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body,
            headers={"content-type":"application/json","x-api-key":ANTHROPIC_KEY,
                     "anthropic-version":"2023-06-01"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                d = json.loads(r.read().decode())
            return "".join(b.get("text","") for b in d.get("content",[]) if b.get("type")=="text")
        except urllib.error.HTTPError as e:
            last = f"anthropic {e.code}: {e.read().decode('utf-8','replace')[:200]}"
            if e.code in (429, 529, 500, 503):      # rate limit / overloaded -> back off
                time.sleep(2 ** attempt * 3); continue
            raise RuntimeError(last)                 # 400/401/404 -> real error, stop
    raise RuntimeError(last or "anthropic: retries exhausted")

def classify_calls(sents):
    """LLM pass over timestamped sentences -> clean calls. None = caller uses heuristic."""
    if not ANTHROPIC_KEY or not sents:
        return None
    text = "\n".join(f"[{int(t)}s] {s}" for t, s in sents)
    out = []; ok = False
    for i in range(0, len(text), 9000):
        try:
            raw = _anthropic(text[i:i+9000], CLASSIFY_SYS, 1500)
            raw = raw[raw.find("["): raw.rfind("]")+1]
            for c in json.loads(raw):
                tk = str(c.get("ticker","")).upper().strip()
                if not tk: continue
                secs = int(c.get("secs", 0) or 0)
                out.append({"tk": tk,
                            "dir": "S" if str(c.get("direction","")).lower().startswith("s") else "L",
                            "conviction": c.get("conviction","medium"),
                            "quote": str(c.get("quote",""))[:160],
                            "secs": secs, "ts": mmss(secs)})
            ok = True
        except Exception as e:
            print(f"  ! classify chunk failed ({e})", file=sys.stderr)
    if not ok:
        return None   # every chunk failed -> fall back to heuristic, do not wipe
    seen, uniq = set(), []
    for c in out:
        if c["tk"] in seen: continue
        seen.add(c["tk"]); uniq.append(c)
    return uniq

# ====================  STAGE 3 : GRADE (mark to market)  ====================
# No fake "closed". Every call carries its return since it was made, in its stated
# direction, off real daily closes. The market grades the call, live, forever.
_CLOSES = {}
def _close_series(ticker):
    if ticker in _CLOSES: return _CLOSES[ticker]
    try:
        raw = _coingecko(ticker, days=365) if ticker in CRYPTO else _stooq(ticker)
        s = [(d, c) for d, o, h, l, c in raw]
    except Exception:
        s = []
    _CLOSES[ticker] = s
    return s

def grade(ticker, direction, call_date_iso):
    """Return since the call, in its stated direction. None if no price coverage."""
    s = _close_series(ticker)
    if not s: return None
    entry = next((c for d, c in s if d >= call_date_iso), None)
    if entry is None: entry = s[-1][1]
    last = s[-1][1]
    if not entry: return None
    raw = (last - entry) / entry
    ret = raw if direction == "L" else -raw
    return {"entry_px": round(entry, 4), "last_px": round(last, 4),
            "ret": round(ret * 100, 1), "green": ret > 0}


def build(all_episodes=False, limit=20):
    eps = episodes()
    if not all_episodes:
        eps = eps[:limit]
    feed, tape, plans = [], [], {}
    greens = reds = 0; ret_sum = 0.0; graded_n = 0
    for ep in eps:
        print(f"- {ep['pubDate'][:16]}  {ep['title'][:60]}")
        try:
            tj = get(ep["transcript"], asjson=True)
        except Exception as e:
            print(f"  ! no transcript yet ({e})"); 
            calls, sents = extract_calls_heuristic([(0, ep["desc"])]), []
            tj = None
        else:
            calls, sents = extract(ep["title"], ep["desc"], tj)
        # Heuristic calls = the broad, searchable layer (drives the TAPE / search).
        raw_calls = calls
        # Stage 2: clean calls for the FEED / scoreboard. Falls back to heuristic
        # if no key or the pass fails (never wipes the broad layer).
        clf = classify_calls(sents)
        feed_calls = clf if clf else raw_calls
        if clf:
            print(f"  classified {len(clf)} real calls")
        # date label + iso for grading
        try:
            dt = datetime.strptime(ep["pubDate"][:16].strip(), "%a, %d %b %Y")
            dlabel = dt.strftime("%b %d"); call_iso = dt.strftime("%Y-%m-%d")
        except Exception:
            dlabel = ep["pubDate"][:11]; call_iso = "1970-01-01"
        # spoken levels (only where stated on air) come from the heuristic extract
        for c in raw_calls:
            lv = c.get("levels")
            if lv and lv.get("stated") and c["tk"] not in plans:
                plans[c["tk"]] = {"entry": lv["entry"], "stop": lv["stop"],
                                  "target": lv["target"], "date": dlabel, "ep": ep["id"]}
        # Stage 3: grade each FEED call live, mark to market
        for c in feed_calls:
            g = grade(c["tk"], c.get("dir","L"), call_iso)
            if g:
                c["status"] = "green" if g["green"] else "red"
                c["pct"] = f"{'+' if g['ret']>=0 else ''}{g['ret']}%"
                c["ret"] = g["ret"]
                graded_n += 1; ret_sum += g["ret"]
                greens += 1 if g["green"] else 0
                reds   += 0 if g["green"] else 1
            else:
                c["status"] = "open"; c["pct"] = ""
        # feed row: [tk, dir, status, pct, thesis, conviction]
        rows = [[c["tk"], c.get("dir","L"), c.get("status","open"), c.get("pct",""),
                 c.get("thesis", c.get("quote",""))[:120], c.get("conviction","")] for c in feed_calls]
        feed.append({"d": dlabel, "t": ep["title"], "g": "", "calls": rows})
        # tape receipts (broad, searchable): [tk, quote, ts, epid, secs, date]
        for c in raw_calls:
            if c.get("ts"):
                secs = int(c.get("secs", 0))
                tape.append([c["tk"], c.get("quote","")[:160], c["ts"], ep["id"], secs, dlabel])
        # 4: attach prices for studied tickers
        # for c in calls: c["prices"] = price_window(c["tk"], ep["pubDate"])
    graded = greens + reds
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "counterparty public podcast feed (buzzsprout) + own extraction",
        "feed": feed,
        "tape": tape,
        "plans": plans,   # entry/stop/target ONLY where spoken on air, else null
        "charts": {},   # real OHLC per studied ticker, for the Anatomy charts
        "stats": {       # live, marked to market, recomputed each run
            "green_now": (round(100 * greens / graded) if graded else None),
            "avg_ret":   (round(ret_sum / graded_n, 1) if graded_n else None),
            "graded": graded, "open": sum(len(f["calls"]) for f in feed) - graded,
            "shows": len(feed),
        },
        # voices / dispatch left for the editor pass (the human seat)
    }
    STUDIED = ["AMC", "ZEC", "MU", "MSTR", "HYPE", "WTI"]
    for tk in STUDIED:
        series = price_window(tk)
        if series:
            data["charts"][tk] = series
            print(f"  prices {tk}: {len(series)} candles")
    if "--youtube" in sys.argv or all_episodes:
        print("- pulling the YouTube channel as a second source")
        yt = youtube_calls(limit=(50 if all_episodes else 10),
                           since=("2026-01-01" if all_episodes else None))
        if yt:
            data["tape"] = (data.get("tape") or []) + yt
            print(f"  youtube: {len(yt)} timestamped lines")
    # Lexicon: built from the desk's own words across the whole tape
    lex = build_lexicon(data["tape"])
    if lex:
        data["lexicon"] = lex
        print(f"  lexicon: {len(lex)} terms from the corpus")
    with open(OUT, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\nwrote {OUT}: {len(feed)} shows, {len(tape)} receipts"
          + (f", {len(data.get('lexicon',[]))} lexicon terms" if data.get('lexicon') else ""))

if __name__ == "__main__":
    build(all_episodes=("--all" in sys.argv))

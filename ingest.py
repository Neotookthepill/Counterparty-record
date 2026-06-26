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

import json, re, sys, os, urllib.request, xml.etree.ElementTree as ET
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

def youtube_calls(limit=10):
    cid = yt_channel_id()
    if not cid:
        print("  ! could not resolve YouTube channel id", file=sys.stderr); return []
    rows = []
    for v in yt_videos(cid)[:limit]:
        try:
            sents = yt_transcript(v["id"])
        except Exception as e:
            print(f"  ! no captions for {v['id']} ({e})", file=sys.stderr); continue
        for c in extract_calls_heuristic(sents):
            rows.append([c["tk"], c["quote"], c["ts"], "yt:"+v["id"], c["secs"],
                         v["published"][:10]])
    return rows  # deep link: https://www.youtube.com/watch?v=ID&t=NNNs

# ---------- 6 : assemble ----------
def build(all_episodes=False, limit=20):
    eps = episodes()
    if not all_episodes:
        eps = eps[:limit]
    feed, tape, plans = [], [], {}
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
        # date label
        try:
            dt = datetime.strptime(ep["pubDate"][:16].strip(), "%a, %d %b %Y")
            dlabel = dt.strftime("%b %d")
        except Exception:
            dlabel = ep["pubDate"][:11]
        # feed row in the site's shape: [tk, dir, status, pct, thesis]
        for c in calls:
            lv = c.get("levels")
            if lv and lv.get("stated") and c["tk"] not in plans:
                plans[c["tk"]] = {"entry": lv["entry"], "stop": lv["stop"],
                                  "target": lv["target"], "date": dlabel, "ep": ep["id"]}
        rows = [[c["tk"], c.get("dir","L"), c.get("status","open"),
                 c.get("pct",""), c.get("thesis", c.get("quote",""))[:120]] for c in calls]
        feed.append({"d": dlabel, "t": ep["title"], "g": "", "calls": rows})
        # tape receipts: [tk, quote, ts, epid, secs, date]
        for c in calls:
            if c.get("ts"):
                secs = int(c.get("secs", 0))
                tape.append([c["tk"], c.get("quote","")[:160], c["ts"], ep["id"], secs, dlabel])
        # 4: attach prices for studied tickers
        # for c in calls: c["prices"] = price_window(c["tk"], ep["pubDate"])
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "counterparty public podcast feed (buzzsprout) + own extraction",
        "feed": feed,
        "tape": tape,
        "plans": plans,   # entry/stop/target ONLY where spoken on air, else null
        "charts": {},   # real OHLC per studied ticker, for the Anatomy charts
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
        yt = youtube_calls(limit=(50 if all_episodes else 10))
        if yt:
            data["tape"] = (data.get("tape") or []) + yt
            print(f"  youtube: {len(yt)} timestamped lines")
    with open(OUT, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\nwrote {OUT}: {len(feed)} shows, {len(tape)} receipts")

if __name__ == "__main__":
    build(all_episodes=("--all" in sys.argv))

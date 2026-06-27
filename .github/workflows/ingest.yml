name: Refresh The Record

# Pulls Counterparty's archive (Buzzsprout show 2535072 + @notthreadguy YouTube),
# writes the_record_data.json, and deploys to Netlify. No account link needed.
#
# Scheduled runs = incremental (latest shows). To pull the WHOLE archive once,
# run it manually with "Pull the entire archive" checked.

on:
  schedule:
    - cron: "0 9 * * 2-6"   # 09:00 UTC, Tue through Sat (after each show posts)
  workflow_dispatch:
    inputs:
      full:
        description: "Pull the entire archive (all past episodes)"
        type: boolean
        default: false

jobs:
  refresh:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install deps
        run: pip install requests youtube-transcript-api yt-dlp

      - name: Pull episodes
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          if [ "${{ github.event.inputs.full }}" = "true" ]; then
            echo "Full archive backfill"
            python ingest.py --all --youtube
          else
            echo "Incremental (latest shows)"
            python ingest.py --youtube
          fi

      - name: Commit data if it changed
        run: |
          git config user.name "the-record-bot"
          git config user.email "bot@users.noreply.github.com"
          git add the_record_data.json
          git commit -m "data: refresh $(date -u +%F)" || echo "no change"
          git push || echo "nothing to push"

      - name: Deploy to Netlify
        env:
          NETLIFY_AUTH_TOKEN: ${{ secrets.NETLIFY_AUTH_TOKEN }}
          NETLIFY_SITE_ID: ${{ secrets.NETLIFY_SITE_ID }}
        run: |
          npm install -g netlify-cli
          netlify deploy --prod --dir=. --site="$NETLIFY_SITE_ID" --auth="$NETLIFY_AUTH_TOKEN"

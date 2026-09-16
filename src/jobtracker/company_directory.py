"""
A broad list of well-known companies, for the Settings page's company
search suggestions only — not used in scoring, filtering, or anywhere
that assumes a name here actually resolves.

This is a starting point for typing, not a verified catalog: nothing
here has been confirmed to have a live Greenhouse/Lever/Ashby board.
resolve_all() is the only thing that ever actually verifies that, at
add time, exactly as it does for a name typed with no suggestion at
all — a wrong or stale entry here just surfaces the same "couldn't
find a board for X" result a bad guess always has. There is no
comprehensive public index of "every company on these three ATSes" to
draw from instead (Greenhouse alone counts 220,000+ customers) — this
is deliberately a few hundred recognizable names spanning many
industries, so searching turns up something to click for a lot of
common cases, not an attempt at completeness no static list could
honestly claim anyway.
"""

from __future__ import annotations

COMPANY_DIRECTORY: tuple[str, ...] = (
    # Big tech / platforms
    "Google", "Microsoft", "Amazon", "Meta", "Apple", "Netflix", "Adobe",
    "Salesforce", "Oracle", "IBM", "SAP", "ServiceNow", "Workday",
    "Intuit", "Uber", "Lyft", "Airbnb", "Pinterest", "Snap", "Spotify",
    "Dropbox", "Slack", "Zoom", "DocuSign", "Twilio", "Okta", "Atlassian",
    "Shopify", "Etsy", "eBay", "PayPal", "Block", "Square", "Roblox",
    "Unity", "Reddit", "Discord", "X", "LinkedIn", "Yelp", "Zillow",
    "Redfin", "Expedia", "Booking.com", "DoorDash", "Instacart",
    "Postmates", "Grubhub",

    # Cloud / infra / devtools
    "Datadog", "New Relic", "PagerDuty", "HashiCorp", "MongoDB",
    "Elastic", "Confluent", "Snowflake", "Databricks", "Fastly",
    "Cloudflare", "Vercel", "Netlify", "Render", "Fly.io", "CircleCI",
    "GitLab", "GitHub", "JFrog", "Sentry", "Honeycomb", "Grafana Labs",
    "LaunchDarkly", "Postman", "Retool", "Airtable", "Notion", "Linear",
    "Figma", "Canva", "Miro", "Asana", "Monday.com", "ClickUp", "Coda",
    "Segment", "Amplitude", "Mixpanel", "Heap", "Braze", "Iterable",
    "Algolia", "Twilio SendGrid", "Auth0", "1Password", "Cloudinary",
    "Supabase", "PlanetScale", "Temporal", "Chronosphere", "Cockroach Labs",
    "Redis", "Docker", "Buildkite", "Harness",

    # Fintech / crypto
    "Stripe", "Plaid", "Brex", "Ramp", "Mercury", "Chime", "SoFi",
    "Robinhood", "Coinbase", "Kraken", "Circle", "Gemini", "Affirm",
    "Klarna", "Marqeta", "Wealthfront", "Betterment", "Carta",
    "Modern Treasury", "Bill.com", "Toast", "Checkr", "Melio", "Ripple",
    "Chainalysis", "Fireblocks", "Alchemy", "Anchorage Digital",

    # AI / data / ML
    "OpenAI", "Anthropic", "Cohere", "Hugging Face", "Scale AI",
    "Weights & Biases", "Together AI", "Perplexity", "Runway",
    "Stability AI", "Adept", "Character.AI", "Glean", "Sourcegraph",
    "Replit", "Cursor", "Palantir", "C3 AI",

    # Health / biotech
    "Ro", "Hims & Hers", "Oscar Health", "Included Health", "Cedar",
    "Truepill", "Nurx", "Carbon Health", "Devoted Health", "Clover Health",
    "23andMe", "Tempus", "Recursion Pharmaceuticals", "Benchling",
    "Color Health", "Headspace", "Calm", "Noom", "Ginger", "Lyra Health",
    "SonderMind", "Hinge Health",

    # Gaming / media
    "Riot Games", "Epic Games", "Valve", "Twitch",
    "Niantic", "Blizzard Entertainment", "Take-Two Interactive",
    "Electronic Arts", "Activision", "Wizards of the Coast",

    # Delivery / logistics / mobility
    "Flexport", "Samsara", "Nuro", "Waymo", "Cruise", "Zoox", "Rivian",
    "Lucid Motors", "Anduril Industries", "Applied Intuition", "Convoy",
    "Motive", "Bird", "Lime",

    # Enterprise SaaS / HR / productivity
    "Gusto", "Rippling", "Deel", "Remote", "Justworks", "Greenhouse",
    "Lever", "Ashby", "Lattice", "Culture Amp", "BambooHR", "Zenefits",
    "Smartsheet", "Front", "Intercom", "Zendesk", "Drift",
    "Gong", "Outreach", "Clari", "Pendo", "Chorus.ai",

    # Security
    "CrowdStrike", "Palo Alto Networks", "Zscaler", "SentinelOne",
    "Wiz", "Snyk", "Vanta", "Drata", "HackerOne", "Bugcrowd",
    "Tanium", "Verkada",

    # Real estate / proptech
    "Compass", "Opendoor", "Better.com", "Blend", "Divvy Homes",
    "Roofstock",

    # E-commerce / consumer
    "Faire", "Whatnot", "StockX", "Poshmark", "ThredUp", "Chewy",
    "Wayfair", "Warby Parker", "Allbirds", "Glossier", "Away",
    "Squarespace", "Wix", "Webflow", "Klaviyo",

    # Ed-tech
    "Coursera", "Udemy", "Duolingo", "Khan Academy", "Chegg",
    "MasterClass", "Guild Education", "Outschool",

    # Aerospace / defense / hardware
    "SpaceX", "Blue Origin", "Relativity Space", "Boom Supersonic",
    "Skydio", "Shield AI", "iRobot",
)

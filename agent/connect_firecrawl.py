"""One-time setup: connect your Firecrawl account to Composio for the same user_id the agent uses.


Docs: https://docs.composio.dev/docs/manually-authenticating (session.authorize + wait_for_connection)
"""
from composio import Composio
from dotenv import load_dotenv

load_dotenv()
composio = Composio()
session = composio.create(user_id="research-agent", toolkits=["firecrawl"])  # same user_id as composio_tools.py
req = session.authorize("firecrawl")
print("Open this link and finish connecting Firecrawl:\n", req.redirect_url)
acct = req.wait_for_connection(300)
print("Connected:", getattr(acct, "id", acct))
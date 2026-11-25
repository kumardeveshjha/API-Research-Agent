"""All Gemini calls live here, so if the SDK changes you fix ONE file.

Setup: pip install -U google-genai   and put GEMINI_API_KEY in .env  (get a key at aistudio.google.com)
Tries the classic client.models.generate_content(...) first, then the newer client.interactions.create(...)
(the current Gemini quickstart uses interactions). NOT run by the author: test with `python agent/llm.py`.
"""
import concurrent.futures as cf
import os
import time

from dotenv import load_dotenv

load_dotenv()
_client = None


def _c():
    global _client
    if _client is None:
        from google import genai
        from google.genai import types
        key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        # Cap the SDK's OWN retry/backoff so it can never run longer than our outer timeout.
        # Without this, a 429/transient error makes the SDK retry internally for minutes
        # while our code just looks frozen (this was the actual cause of the "freeze").
        http_opts = types.HttpOptions(
            timeout=int(os.getenv("LLM_TIMEOUT", "120")) * 1000,  # ms
            retry_options=types.HttpRetryOptions(attempts=2, initial_delay=2.0, max_delay=10.0),
        )
        _client = genai.Client(api_key=key, http_options=http_opts) if key else genai.Client(http_options=http_opts)
    return _client


def _once(system: str, user: str, model: str, max_tokens: int) -> str:
    c = _c()
    first_err = None
    try:
        from google.genai import types
        cfg = types.GenerateContentConfig(
            system_instruction=system, response_mime_type="application/json",
            max_output_tokens=max_tokens, temperature=0)
        print("    [llm] trying classic generate_content...", flush=True)
        return c.models.generate_content(model=model, contents=user, config=cfg).text
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
            # Quota error: the fallback path uses the SAME quota, so trying it just burns
            # another request for the same failure. Raise immediately so complete() can
            # back off properly instead of doing two failing calls per attempt.
            print(f"    [llm] quota exceeded (429) -> skipping fallback, backing off", flush=True)
            raise
        print(f"    [llm] classic path failed ({msg[:150]}), trying interactions.create...", flush=True)
        first_err = e
    try:  # newer API shown in the current quickstart (system text is prepended; not confirmed separately)
        it = c.interactions.create(
            model=model, input=f"{system}\n\n---\n\n{user}",
            response_format={"type": "text", "mime_type": "application/json"})
        return it.output_text
    except Exception as e2:  # noqa: BLE001
        print(f"    [llm] interactions path ALSO failed ({str(e2)[:150]})", flush=True)
        raise first_err  # report the classic error: usually the more informative one


_POOL = cf.ThreadPoolExecutor(max_workers=4)
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "120"))


def complete(system: str, user: str, model: str, max_tokens: int = 4000, retries: int = 4) -> str:
    """Returns the model's JSON text. Retries on rate limits (free tiers hit them often). Times out instead of hanging."""
    for i in range(retries):
        try:
            t0 = time.time()
            out = _POOL.submit(_once, system, user, model, max_tokens).result(timeout=LLM_TIMEOUT)
            print(f"    [llm] {model} ok in {time.time() - t0:.1f}s", flush=True)
            return out
        except cf.TimeoutError:
            print(f"    [llm] TIMED OUT after {LLM_TIMEOUT}s (attempt {i + 1}/{retries})", flush=True)
            if i == retries - 1:
                raise TimeoutError("LLM call timed out")
            continue
        except Exception as e:  # noqa: BLE001
            print(f"    [llm] error: {str(e)[:150]}", flush=True)
            msg = str(e).lower()
            if i < retries - 1 and any(k in msg for k in ("429", "quota", "resource_exhausted", "rate limit", "503", "unavailable")):
                wait = 20 * (i + 1)  # free-tier quotas often need 20-60s between retries
                print(f"    [llm] waiting {wait}s before retry {i + 2}/{retries}...", flush=True)
                time.sleep(wait)
                continue
            raise


if __name__ == "__main__":
    m = os.getenv("RESEARCH_MODEL", "Gemini 4 Hydrogen")
    print("Model:", m)
    print(complete('Reply with JSON only: {"ok": true}', "ping", m, max_tokens=50))
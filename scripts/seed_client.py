"""Seed client login identities and print their sign-in links.

Usage (from the repo root, same env as the app so LUMNIA_DB matches):

    python -m scripts.seed_client "Kivu Agro" alice@kivu.cd bob@kivu.cd \
        --base-url https://lumnia.example.com

There is deliberately no admin UI for this yet (spec: out of scope until a
real second client exists) — the analyst mints links here or via
POST /clients/{client}/users and sends them personally.
"""
from __future__ import annotations

import argparse

from app import auth, storage


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("client", help="client workspace label, e.g. 'Kivu Agro'")
    p.add_argument("emails", nargs="+", help="login emails to register")
    p.add_argument("--base-url", default="",
                   help="prefix for printed links, e.g. the deployed origin")
    args = p.parse_args()

    for email in args.emails:
        user = storage.add_client_user(args.client, email)
        if user is None:
            print(f"!! {email}: invalid, or already registered to another client")
            continue
        token = auth.make_client_token("link", user["id"], storage.app_secret())
        print(f"{email}  {args.base_url}/portal/login/{token}")


if __name__ == "__main__":
    main()

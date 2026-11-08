"""Give an account that predates sign-in an email and a password, keeping all its work.

    python tools/attach_account.py                          # list the accounts at risk
    python tools/attach_account.py --sid 1 --email you@example.com

The password is typed at the prompt, or read from BRIGHTBOARD_ATTACH_PASSWORD when nobody
is at the keyboard, so it never ends up in a file, a URL or the shell history. It is hashed
with scrypt on the way in, exactly like a sign-up, and the tool signs in with it once to
prove the account is reachable before it reports success.

Which database this writes to is decided by DATABASE_URL the same way the app decides it:
set, and it is that Postgres (the deployed one); unset, and it is profile.db next to the
project. Run it against production only when you mean production.
"""

import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import profile  # noqa: E402


def listing():
    rows = profile.accounts_without_email()
    if not rows:
        print("Every account has an email — nothing to attach.")
        return 0
    where = "the deployed database" if profile.DATABASE_URL else "profile.db (local)"
    print("Accounts nobody can sign in to yet (%s):\n" % where)
    print("  %-5s %-24s %-9s %-10s %s" % ("id", "name", "grade", "curriculum", "lessons+results stay put"))
    for r in rows:
        print("  %-5s %-24s %-9s %-10s" % (r["id"], r["name"][:24], r["grade"], r["curriculum"]))
    print("\nAttach one with:  python tools/attach_account.py --sid <id> --email <address>")
    return 0


def attach(sid, email, password):
    try:
        profile.attach_account(sid, email, password)
    except ValueError as exc:
        print("Refused: %s" % exc)
        return 1
    if profile.sign_in(email, password) != sid:
        print("Attached, but signing in with it failed — check the email and try again.")
        return 1
    print("Signed in as %s via %s. Old lessons, papers and analytics are all still there."
          % (sid, email))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sid", type=int, help="the account id to attach")
    ap.add_argument("--email", help="the address it will be signed in with")
    a = ap.parse_args()
    if bool(a.sid) != bool(a.email):
        ap.error("--sid and --email go together")
    if not a.sid:
        return listing()
    # A password on the command line would sit in the shell history; the environment
    # variable is there for runs with no one at the keyboard, and typing it twice is
    # for the ones with someone at the keyboard.
    from_env = os.environ.get("BRIGHTBOARD_ATTACH_PASSWORD")
    if from_env:
        print("Using the password in BRIGHTBOARD_ATTACH_PASSWORD.")
        return attach(a.sid, a.email, from_env)
    password = getpass.getpass("Password for %s: " % a.email)
    if getpass.getpass("Type it again to be sure: ") != password:
        print("The two entries did not match — nothing was changed.")
        return 1
    return attach(a.sid, a.email, password)


if __name__ == "__main__":
    sys.exit(main())

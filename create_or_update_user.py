import argparse

from utils import create_or_update_auth_user

def main():
    parser = argparse.ArgumentParser(description="Create or update a Mailing-lists auth user.")
    parser.add_argument("--username", required=True, help="Username to create or update.")
    parser.add_argument("--password", required=True, help="Plain-text password to hash and store.")
    parser.add_argument(
        "--inactive",
        action="store_true",
        help="Mark the user as inactive.",
    )
    args = parser.parse_args()

    saved = create_or_update_auth_user(
        username=args.username,
        password=args.password,
        active=not args.inactive,
    )
    print(f"User '{saved['username']}' saved in Firestore. active={saved.get('active', True)}")


if __name__ == "__main__":
    main()

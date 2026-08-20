"""Register the approvals Lambda as the Telegram bot's webhook.

Generates a fresh webhook secret, stores it in SSM (SecureString), and calls
setWebhook so Telegram sends callback taps to the function URL with that
secret in the X-Telegram-Bot-Api-Secret-Token header. Idempotent: rerunning
rotates the secret and re-registers. Note: the approvals Lambda caches SSM
values per container, so after a rotation give warm Lambdas a few minutes to
recycle (or redeploy) before expecting the new secret to validate.

Usage:
    AWS_PROFILE=agentlab uv run python scripts/register_telegram_webhook.py \
        --url "$(terraform -chdir=infra output -raw approvals_webhook_url)"
"""

import argparse
import secrets

import boto3
import httpx

TOKEN_PARAM = "/agentlab/telegram/bot-token"
SECRET_PARAM = "/agentlab/telegram/webhook-secret"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="The Lambda function URL.")
    args = parser.parse_args()

    ssm = boto3.client("ssm")
    token = ssm.get_parameter(Name=TOKEN_PARAM, WithDecryption=True)["Parameter"][
        "Value"
    ]
    webhook_secret = secrets.token_urlsafe(32)
    ssm.put_parameter(
        Name=SECRET_PARAM, Value=webhook_secret, Type="SecureString", Overwrite=True
    )

    response = httpx.post(
        f"https://api.telegram.org/bot{token}/setWebhook",
        json={
            "url": args.url,
            "secret_token": webhook_secret,
            "allowed_updates": ["callback_query"],
        },
        timeout=30,
    )
    response.raise_for_status()
    print("setWebhook:", response.json())

    info = httpx.get(
        f"https://api.telegram.org/bot{token}/getWebhookInfo", timeout=30
    )
    print("getWebhookInfo:", info.json())


if __name__ == "__main__":
    main()

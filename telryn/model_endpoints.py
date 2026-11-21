"""Opt-in, customer-scoped inference for already redacted transcript passages."""

import http.client
import json
import os
import re

from .contracts import InputError, canonical, read_json
from .screen import _unique_keys

MAX_RESPONSE = 1_000_000
MAX_OUTPUT_TOKENS = 1300
BEDROCK_OUTPUT = (
    "\nRespond with a single JSON object and no commentary, following this schema:\n"
)


def _match(value, pattern, field):
    if not isinstance(value, str) or not re.fullmatch(pattern, value, re.ASCII):
        raise InputError(f"invalid {field} in cloud model configuration")
    return value


class AzureOpenAI:
    def __init__(self, config, model):
        resource = _match(
            config["resource"], r"[a-z0-9][a-z0-9-]{0,61}[a-z0-9]", "Azure resource"
        )
        self.host = resource + ".openai.azure.com"
        self.model = _match(
            model, r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", "Azure deployment"
        )
        self.revision = _match(
            config["model_revision"],
            r"[A-Za-z0-9_.-]+-\d{4}-\d{2}-\d{2}",
            "dated Azure model revision",
        )
        self.identity = {
            **config,
            "host": self.host,
            "deployment": self.model,
            "api": "openai/v1/chat/completions",
            "max_completion_tokens": MAX_OUTPUT_TOKENS,
        }
        key = os.environ.get("AZURE_OPENAI_API_KEY")
        token = os.environ.get("TELRYN_AZURE_ACCESS_TOKEN")
        if bool(key) == bool(token):
            raise InputError(
                "set one of AZURE_OPENAI_API_KEY or TELRYN_AZURE_ACCESS_TOKEN"
            )
        if not re.fullmatch(r"[!-~]+", key or token):
            raise InputError("invalid Azure credential")
        self.auth = {"api-key": key} if key else {"Authorization": "Bearer " + token}

    def verify(self):
        # The data-plane response identifies the actual model on every completion.
        pass

    def complete(self, prompt, visible, schema):
        data = {
            "model": self.model,
            "max_completion_tokens": MAX_OUTPUT_TOKENS,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "telryn_cited_facts",
                    "strict": True,
                    "schema": schema,
                },
            },
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": canonical(visible)},
            ],
        }
        conn = http.client.HTTPSConnection(self.host, timeout=120)
        try:
            conn.request(
                "POST",
                "/openai/v1/chat/completions",
                body=canonical(data),
                headers={"Content-Type": "application/json", **self.auth},
            )
            response = conn.getresponse()
            if response.status != 200:
                raise InputError(f"Azure OpenAI returned HTTP {response.status}")
            raw = response.read(MAX_RESPONSE + 1)
        except (OSError, http.client.HTTPException) as exc:
            raise InputError("Azure OpenAI connection failed") from exc
        finally:
            conn.close()
        if len(raw) > MAX_RESPONSE:
            raise InputError("Azure OpenAI response exceeded the size limit")
        result = json.loads(raw, object_pairs_hook=_unique_keys)
        if not isinstance(result, dict) or result.get("model") != self.revision:
            raise InputError("Azure response does not match the pinned model revision")
        choices = result.get("choices")
        if (
            not isinstance(choices, list)
            or len(choices) != 1
            or not isinstance(choices[0], dict)
        ):
            raise InputError("invalid Azure OpenAI completion")
        msg = choices[0].get("message")
        if (
            choices[0].get("finish_reason") != "stop"
            or not isinstance(msg, dict)
            or msg.get("role") != "assistant"
            or msg.get("refusal")
            or not isinstance(msg.get("content"), str)
        ):
            raise InputError("incomplete Azure OpenAI completion")
        return msg["content"]


def _aws_clients(region):
    try:
        import boto3
        from botocore.config import Config
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError as exc:
        raise InputError(
            "Bedrock requires the telryn[bedrock] extra or the SDK in requirements-bedrock.txt"
        ) from exc
    errors = (BotoCoreError, ClientError)
    try:
        session = boto3.Session(region_name=region)
        if region not in session.get_available_regions("sts", partition_name="aws"):
            raise InputError("select an AWS commercial region")
        config = Config(
            signature_version="v4",
            connect_timeout=10,
            read_timeout=120,
            retries={"mode": "standard", "total_max_attempts": 1},
        )
        sts = session.client(
            "sts", endpoint_url=f"https://sts.{region}.amazonaws.com", config=config
        )
        runtime = session.client(
            "bedrock-runtime",
            endpoint_url=f"https://bedrock-runtime.{region}.amazonaws.com",
            config=config,
        )
    except errors as exc:
        raise InputError("AWS could not initialize the scoped Bedrock clients") from exc
    return sts, runtime, errors


class Bedrock:
    def __init__(self, config, model):
        region = _match(config["region"], r"[a-z]{2}-[a-z]+-\d+", "AWS region")
        self.account = _match(config["account_id"], r"\d{12}", "AWS account ID")
        model = _match(
            model,
            r"[a-z0-9]+\.[a-z0-9][a-z0-9.-]*:[0-9]+",
            "versioned AWS foundation model ID",
        )
        self.model_arn = f"arn:aws:bedrock:{region}::foundation-model/{model}"
        self.identity = {
            **config,
            "foundation_model_arn": self.model_arn,
            "api": "Converse",
            "maxTokens": MAX_OUTPUT_TOKENS,
            "temperature": 0,
            "output_instruction": BEDROCK_OUTPUT,
        }
        self.sts, self.runtime, self.errors = _aws_clients(region)

    def verify(self):
        try:
            caller = self.sts.get_caller_identity()
        except self.errors as exc:
            raise InputError(
                "AWS STS could not verify the Bedrock caller account"
            ) from exc
        if caller.get("Account") != self.account:
            raise InputError("AWS credentials belong to a different account")

    def complete(self, prompt, visible, schema):
        try:
            result = self.runtime.converse(
                modelId=self.model_arn,
                system=[{"text": prompt + BEDROCK_OUTPUT + canonical(schema)}],
                messages=[{"role": "user", "content": [{"text": canonical(visible)}]}],
                inferenceConfig={"maxTokens": MAX_OUTPUT_TOKENS, "temperature": 0},
            )
        except self.errors as exc:
            raise InputError(
                "Bedrock Converse failed; check model access, region and deployment logs"
            ) from exc
        output = result.get("output")
        msg = output.get("message") if isinstance(output, dict) else None
        blocks = msg.get("content") if isinstance(msg, dict) else None
        if (
            result.get("stopReason") != "end_turn"
            or not isinstance(msg, dict)
            or msg.get("role") != "assistant"
            or not isinstance(blocks, list)
            or len(blocks) != 1
            or not isinstance(blocks[0], dict)
            or set(blocks[0]) != {"text"}
            or not isinstance(blocks[0]["text"], str)
            or len(blocks[0]["text"].encode("utf-8")) > MAX_RESPONSE
        ):
            raise InputError("incomplete Bedrock completion")
        return blocks[0]["text"]


def load(path, model):
    config = read_json(path, "cloud model endpoint")
    provider = config.get("provider")
    fields = set(config) - {"schema", "provider"}
    if provider == "azure-openai" and fields == {"resource", "model_revision"}:
        return AzureOpenAI(config, model)
    if provider == "bedrock" and fields == {"account_id", "region"}:
        return Bedrock(config, model)
    raise InputError(
        "use an azure-openai or bedrock configuration with only its documented fields"
    )

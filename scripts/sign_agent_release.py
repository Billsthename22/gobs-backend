#!/usr/bin/env python3
"""Offline signing tool. Private keys never belong on the API or devices."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat, load_pem_private_key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate-key", help="Create a private signing key and print its public key")
    generate.add_argument("--output", type=Path, required=True)
    sign = commands.add_parser("sign", help="Sign a raw agent binary release, not an installer")
    sign.add_argument("--key", type=Path, required=True)
    sign.add_argument("--artifact", type=Path, required=True)
    sign.add_argument("--version", required=True)
    sign.add_argument("--platform", choices=["windows", "macos"], required=True)
    sign.add_argument("--architecture", choices=["x86_64", "aarch64"], required=True)
    sign.add_argument("--url", required=True)
    sign.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "generate-key":
        key = Ed25519PrivateKey.generate()
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
        print(base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode())
        return
    key = load_pem_private_key(args.key.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        parser.error("An Ed25519 private key is required")
    size = args.artifact.stat().st_size
    if not 0 < size <= 250_000_000:
        parser.error("Artifact must contain 1–250,000,000 bytes")
    digest = hashlib.sha256()
    with args.artifact.open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    manifest = dict(version=args.version, platform=args.platform, architecture=args.architecture,
                    url=args.url, sha256=digest.hexdigest(), size=size)
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    manifest["signature"] = base64.b64encode(key.sign(payload)).decode()
    args.output.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Signed release manifest written to {args.output}")


if __name__ == "__main__":
    main()

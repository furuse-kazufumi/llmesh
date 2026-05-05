"""gen_certs.py — Generate CA and per-node TLS certificates for LLMesh.

Usage:
    # 1. Generate CA (once, by the admin):
    python scripts/gen_certs.py ca --out certs/

    # 2. Generate per-node cert (once per node):
    python scripts/gen_certs.py node --name node-a --ca-dir certs/ --out certs/node-a/

Outputs (CA):
    certs/ca.key   — CA private key  (keep SECRET, never distribute)
    certs/ca.crt   — CA certificate  (distribute to ALL nodes)

Outputs (node):
    certs/node-a/node.key  — node private key (keep on this node only)
    certs/node-a/node.crt  — node certificate (share with peers)
"""
import argparse
import datetime
import ipaddress
import sys
from pathlib import Path

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
except ImportError:
    print("ERROR: 'cryptography' package required.  pip install cryptography")
    sys.exit(1)

_VALIDITY_DAYS = 3650  # 10 years


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _gen_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)
    print(f"  wrote: {path}")


def cmd_ca(out_dir: Path) -> None:
    """Generate a self-signed CA key + certificate."""
    key = _gen_key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "LLMesh CA")])
    now = _now()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=_VALIDITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    _write(
        out_dir / "ca.key",
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ),
    )
    _write(
        out_dir / "ca.crt",
        cert.public_bytes(serialization.Encoding.PEM),
        mode=0o644,
    )
    print("\nCA generated.  Distribute ca.crt to all nodes.  Keep ca.key SECRET.")


def cmd_node(name: str, ca_dir: Path, out_dir: Path, ip: str | None) -> None:
    """Generate a node key + certificate signed by the CA."""
    ca_key_pem = (ca_dir / "ca.key").read_bytes()
    ca_crt_pem = (ca_dir / "ca.crt").read_bytes()
    ca_key = serialization.load_pem_private_key(ca_key_pem, password=None)
    ca_cert = x509.load_pem_x509_certificate(ca_crt_pem)

    node_key = _gen_key()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = _now()

    san_list: list[x509.GeneralName] = [x509.DNSName(name), x509.DNSName("localhost")]
    if ip:
        san_list.append(x509.IPAddress(ipaddress.ip_address(ip)))

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(node_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=_VALIDITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName(san_list), critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    _write(out_dir / "node.key", node_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    _write(out_dir / "node.crt", cert.public_bytes(serialization.Encoding.PEM), mode=0o644)
    print(f"\nNode cert '{name}' generated.  Copy node.key + node.crt to the node machine.")


def main() -> None:
    p = argparse.ArgumentParser(description="LLMesh TLS certificate generator")
    sub = p.add_subparsers(dest="cmd", required=True)

    ca_p = sub.add_parser("ca", help="Generate CA key + certificate")
    ca_p.add_argument("--out", default="certs", help="Output directory (default: certs/)")

    node_p = sub.add_parser("node", help="Generate node key + certificate")
    node_p.add_argument("--name", required=True, help="Node name (used as CN and DNS SAN)")
    node_p.add_argument("--ip", default=None, help="Node IP address (added as IP SAN)")
    node_p.add_argument("--ca-dir", default="certs", help="Directory containing ca.key + ca.crt")
    node_p.add_argument("--out", default=None, help="Output directory (default: certs/<name>/)")

    args = p.parse_args()

    if args.cmd == "ca":
        cmd_ca(Path(args.out))
    elif args.cmd == "node":
        out = Path(args.out) if args.out else Path("certs") / args.name
        cmd_node(args.name, Path(args.ca_dir), out, args.ip)


if __name__ == "__main__":
    main()

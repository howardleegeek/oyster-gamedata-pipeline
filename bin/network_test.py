#!/usr/bin/env python3
"""G006 · bin/network_test.py — Vendor network upload speed + latency + DNS check."""

import argparse
import os
import socket
import sys
import tempfile
import time
from typing import List, Tuple


def measure_dns(hostname: str, timeout: float = 5.0) -> Tuple[bool, float, str]:
    """Measure DNS resolution time. Returns (success, elapsed_s, ip_or_error)."""
    start = time.perf_counter()
    try:
        socket.setdefaulttimeout(timeout)
        return True, time.perf_counter() - start, socket.gethostbyname(hostname)
    except socket.error as exc:
        return False, time.perf_counter() - start, str(exc)


def measure_latency(host: str, port: int = 443, timeout: float = 5.0) -> Tuple[bool, float]:
    """Measure TCP connection latency. Returns (success, elapsed_s)."""
    start = time.perf_counter()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect((host, port))
        return True, time.perf_counter() - start
    except socket.error:
        return False, time.perf_counter() - start
    finally:
        sock.close()


def measure_upload(host: str, port: int = 443, size_kb: int = 100,
                   timeout: float = 10.0) -> Tuple[bool, float, float]:
    """Measure upload throughput via raw TCP. Returns (success, speed_mbps, elapsed_s)."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = os.path.join(tmpdir, "test_data.bin")
            with open(test_file, "wb") as fh:
                fh.write(os.urandom(size_kb * 1024))
            start = time.perf_counter()
            total_sent = 0
            with open(test_file, "rb") as fh:
                while True:
                    chunk = fh.read(8192)
                    if not chunk:
                        break
                    sock.sendall(chunk)
                    total_sent += len(chunk)
            elapsed = time.perf_counter() - start
            sock.close()
            speed = (total_sent * 8) / (elapsed * 1_000_000) if elapsed > 0 else 0.0
            return True, speed, elapsed
    except socket.error:
        return False, 0.0, 0.0


def run_tests(dns_hosts: List[str], latency_host: str, latency_port: int,
              upload_host: str, upload_port: int, upload_size_kb: int,
              timeout: float) -> int:
    """Run all network tests. Returns 0 if all pass, 1 otherwise."""
    print("=" * 60 + "\nNetwork Connectivity Test\n" + "=" * 60)
    all_passed = True

    print("\n[DNS Resolution Tests]")
    for host in dns_hosts:
        ok, elapsed, result = measure_dns(host, timeout)
        if not ok:
            all_passed = False
        print(f"  {host}: {'PASS' if ok else 'FAIL'} ({elapsed*1000:.1f}ms) -> {result}")

    print(f"\n[Latency Test] -> {latency_host}:{latency_port}")
    ok, elapsed = measure_latency(latency_host, latency_port, timeout)
    if not ok:
        all_passed = False
    print(f"  {latency_host}:{latency_port}: {'PASS' if ok else 'FAIL'} ({elapsed*1000:.1f}ms)")

    print(f"\n[Upload Test] -> {upload_host}:{upload_port} ({upload_size_kb}KB)")
    ok, speed, elapsed = measure_upload(upload_host, upload_port, upload_size_kb, timeout)
    if not ok:
        all_passed = False
    print(
        f"  {upload_host}:{upload_port}: {'PASS' if ok else 'FAIL'} "
        f"({speed:.2f} Mbps, {elapsed:.3f}s)"
    )

    print("\n" + "=" * 60)
    print(f"Overall: {'ALL PASSED' if all_passed else 'SOME FAILED'}")
    print("=" * 60)
    return 0 if all_passed else 1


def main(argv: List[str] | None = None) -> int:
    """CLI entry point with argparse. Returns exit code."""
    parser = argparse.ArgumentParser(
        description="Vendor network upload speed + latency + DNS resolution check."
    )
    parser.add_argument("--dns-hosts", nargs="+",
                        default=["google.com", "cloudflare.com", "baidu.com"])
    parser.add_argument("--latency-host", default="google.com")
    parser.add_argument("--latency-port", type=int, default=443)
    parser.add_argument("--upload-host", default="google.com")
    parser.add_argument("--upload-port", type=int, default=443)
    parser.add_argument("--upload-size", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args(argv)
    return run_tests(
        dns_hosts=args.dns_hosts, latency_host=args.latency_host,
        latency_port=args.latency_port, upload_host=args.upload_host,
        upload_port=args.upload_port, upload_size_kb=args.upload_size,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    sys.exit(main())

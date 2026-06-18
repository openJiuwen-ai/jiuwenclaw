#!/usr/bin/env python3
"""Calculate cryptographic hash values for files or text strings."""

import hashlib
import sys
import os

ALGORITHMS = {
    "md5": hashlib.md5,
    "sha1": hashlib.sha1,
    "sha256": hashlib.sha256,
    "sha512": hashlib.sha512,
}

CHUNK_SIZE = 8192  # 8KB chunks for large file reading


def hash_file(filepath, algorithm="sha256"):
    """Compute hash of a file using chunked reading."""
    if not os.path.exists(filepath):
        print(f"ERROR: File not found: {filepath}")
        sys.exit(1)

    h = ALGORITHMS[algorithm]()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def hash_text(text, algorithm="sha256"):
    """Compute hash of a text string."""
    h = ALGORITHMS[algorithm]()
    h.update(text.encode("utf-8"))
    return h.hexdigest()


def format_size(filepath):
    """Format file size in human-readable form."""
    size = os.path.getsize(filepath)
    if size < 1024:
        return f"{size} bytes"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size / (1024*1024):.1f} MB"
    else:
        return f"{size / (1024*1024*1024):.2f} GB"


def compute_hash(input_type, input_value, algorithm="sha256", show_all=False):
    """Main hash computation logic."""
    if show_all:
        algos_to_run = list(ALGORITHMS.keys())
    else:
        if algorithm not in ALGORITHMS:
            print(f"ERROR: Unknown algorithm '{algorithm}'. Use: {', '.join(ALGORITHMS.keys())}")
            sys.exit(1)
        algos_to_run = [algorithm]

    results = {}
    for algo in algos_to_run:
        if input_type == "file":
            results[algo] = hash_file(input_value, algo)
        else:
            results[algo] = hash_text(input_value, algo)

    # Output
    if input_type == "file":
        print(f"文件: {os.path.basename(input_value)}")
    else:
        text_preview = input_value[:50] + "..." if len(input_value) > 50 else input_value
        print(f"文本: {text_preview}")

    for algo, digest in results.items():
        label = algo.upper().replace("SHA", "SHA-")
        print(f"{label}: {digest}")

    if input_type == "file":
        print(f"大小: {format_size(input_value)}")


def verify_files(file_a, file_b, algorithm="sha256"):
    """Verify if two files have the same hash."""
    hash_a = hash_file(file_a, algorithm)
    hash_b = hash_file(file_b, algorithm)

    label = algorithm.upper().replace("SHA", "SHA-")
    if hash_a == hash_b:
        print(f"✅ 文件一致 ({label}: {hash_a})")
    else:
        print(f"❌ 文件不一致")
        print(f"  {os.path.basename(file_a)}: {hash_a}")
        print(f"  {os.path.basename(file_b)}: {hash_b}")


def main():
    if len(sys.argv) < 3:
        print("Usage: hash_calc.py <file|text|verify> <path_or_text> [--algorithm <algo>] [--all]")
        sys.exit(1)

    mode = sys.argv[1]
    algorithm = "sha256"
    show_all = False

    if "--algorithm" in sys.argv:
        idx = sys.argv.index("--algorithm")
        if idx + 1 < len(sys.argv):
            algorithm = sys.argv[idx + 1].lower()

    if "--all" in sys.argv:
        show_all = True

    if mode == "verify":
        if len(sys.argv) < 4:
            print("Usage: hash_calc.py verify <file_a> <file_b>")
            sys.exit(1)
        verify_files(sys.argv[2], sys.argv[3], algorithm)
    elif mode in ("file", "text"):
        compute_hash(mode, sys.argv[2], algorithm, show_all)
    else:
        print(f"ERROR: Unknown mode '{mode}'. Use: file, text, or verify")
        sys.exit(1)


if __name__ == "__main__":
    main()

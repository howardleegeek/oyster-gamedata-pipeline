#!/usr/bin/env python3
"""
Edge test for negative timestamps and pre-2020 date validation.

This module tests boundary conditions for timestamp validation, specifically:
- Negative epoch timestamps (pre-1970-01-01)
- Dates before 2020 that should be explicitly rejected
- Edge cases around the Unix epoch and year boundaries

The schema being tested must reject timestamps before 2020 explicitly.
"""

import argparse
import datetime
import json
import sys
from typing import Any, Dict, List, Optional


def validate_timestamp_schema(timestamp: Any) -> bool:
    """
    Validate a timestamp against the schema requirements.

    The schema must:
    1. Reject timestamps before 2020 explicitly
    2. Handle negative epoch values (pre-1970-01-01)
    3. Accept valid timestamps from 2020 onward

    Args:
        timestamp: The timestamp to validate, can be int, float, str, or datetime

    Returns:
        bool: True if timestamp is valid according to schema, False otherwise
    """
    try:
        # Convert timestamp to datetime for validation
        dt = None

        if isinstance(timestamp, (int, float)):
            # Handle numeric timestamps (seconds since epoch)
            if timestamp < 0:
                # Negative epoch - pre-1970 dates
                # These should be rejected if they represent dates before 2020
                dt = datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc)
            else:
                dt = datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc)

        elif isinstance(timestamp, str):
            # Try to parse string timestamps
            # Common formats: ISO 8601, RFC 3339
            try:
                dt = datetime.datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            except ValueError:
                # Try other common formats
                for fmt in ['%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S',
                           '%Y-%m-%d', '%Y/%m/%d %H:%M:%S']:
                    try:
                        dt = datetime.datetime.strptime(timestamp, fmt)
                        break
                    except ValueError:
                        continue
                if dt is None:
                    return False

        elif isinstance(timestamp, datetime.datetime):
            dt = timestamp
        else:
            # Unsupported type
            return False

        # Check if datetime has timezone info
        if dt.tzinfo is None:
            # Assume UTC if no timezone
            dt = dt.replace(tzinfo=datetime.timezone.utc)

        # Schema requirement: explicitly reject pre-2020 dates
        if dt.year < 2020:
            return False

        # Additional validation: must be a reasonable date (not too far in future)
        current_year = datetime.datetime.now(datetime.timezone.utc).year
        if dt.year > current_year + 10:  # Allow up to 10 years in future
            return False

        return True

    except (ValueError, OverflowError, OSError):
        # Invalid timestamp (e.g., out of range for platform)
        return False


def test_negative_epoch_timestamps() -> List[Dict[str, Any]]:
    """
    Test negative epoch timestamps (pre-1970-01-01).

    Returns:
        List of test cases with results
    """
    test_cases = []

    # Classic negative epoch values
    negative_timestamps = [
        # Pre-1970 dates that should be rejected (before 2020)
        (-31536000, "1969-01-01 00:00:00"),  # 1 year before epoch
        (-63115200, "1968-01-01 00:00:00"),  # 2 years before epoch
        (-94608000, "1967-01-01 00:00:00"),  # 3 years before epoch
        (-126230400, "1966-01-01 00:00:00"), # 4 years before epoch

        # Very old dates
        (-2208988800, "1900-01-01 00:00:00"),
        (-2840140800, "1800-01-01 00:00:00"),

        # Edge case: exactly at epoch (0)
        (0, "1970-01-01 00:00:00 UTC"),

        # Small negative values (just before epoch)
        (-1, "1969-12-31 23:59:59"),
        (-60, "1969-12-31 23:59:00"),
        (-3600, "1969-12-31 23:00:00"),
        (-86400, "1969-12-31 00:00:00"),
    ]

    for ts, description in negative_timestamps:
        is_valid = validate_timestamp_schema(ts)
        expected = False  # All pre-2020 dates should be rejected
        test_cases.append({
            "timestamp": ts,
            "description": description,
            "is_valid": is_valid,
            "expected": expected,
            "passed": is_valid == expected
        })

    return test_cases


def test_pre_2020_dates() -> List[Dict[str, Any]]:
    """
    Test dates before 2020 that should be explicitly rejected.

    Returns:
        List of test cases with results
    """
    test_cases = []

    # Pre-2020 dates in various formats
    pre_2020_dates = [
        # Integer timestamps
        (1577836800, "2020-01-01 00:00:00 UTC"),  # Boundary: should pass
        (1577836799, "2019-12-31 23:59:59 UTC"),  # Just before 2020: should fail
        (1546300800, "2019-01-01 00:00:00 UTC"),
        (1514764800, "2018-01-01 00:00:00 UTC"),
        (1483228800, "2017-01-01 00:00:00 UTC"),

        # String dates
        ("2020-01-01T00:00:00Z", "ISO 8601 2020 boundary"),
        ("2019-12-31T23:59:59Z", "ISO 8601 just before 2020"),
        ("2019-01-01", "Date only 2019"),
        ("2018-12-25 12:00:00", "Christmas 2018"),

        # Python datetime objects
        (datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc), "Python datetime 2020"),
        (datetime.datetime(2019, 12, 31, 23, 59, 59, tzinfo=datetime.timezone.utc), "Python datetime pre-2020"),
    ]

    for ts, description in pre_2020_dates:
        is_valid = validate_timestamp_schema(ts)
        # Determine expected based on whether it's 2020 or later
        if isinstance(ts, (int, float)):
            dt = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
            expected = dt.year >= 2020
        elif isinstance(ts, str):
            # Parse year from string
            year_str = ts[:4] if len(ts) >= 4 else "0000"
            try:
                year = int(year_str)
                expected = year >= 2020
            except ValueError:
                expected = False
        elif isinstance(ts, datetime.datetime):
            expected = ts.year >= 2020
        else:
            expected = False

        test_cases.append({
            "timestamp": ts,
            "description": description,
            "is_valid": is_valid,
            "expected": expected,
            "passed": is_valid == expected
        })

    return test_cases


def test_valid_post_2020_dates() -> List[Dict[str, Any]]:
    """
    Test valid dates from 2020 onward.

    Returns:
        List of test cases with results
    """
    test_cases = []

    valid_dates = [
        # 2020 dates
        (1577836800, "2020-01-01 00:00:00 UTC"),
        (1580515200, "2020-02-01 00:00:00 UTC"),
        (1585699200, "2020-04-01 00:00:00 UTC"),

        # 2021 dates
        (1609459200, "2021-01-01 00:00:00 UTC"),

        # 2022 dates
        (1640995200, "2022-01-01 00:00:00 UTC"),

        # 2023 dates
        (1672531200, "2023-01-01 00:00:00 UTC"),

        # 2024 dates
        (1704067200, "2024-01-01 00:00:00 UTC"),

        # Near future (within reasonable bounds)
        (datetime.datetime.now(datetime.timezone.utc), "Current time"),

        # String formats
        ("2020-06-15T14:30:00Z", "Mid-2020 ISO"),
        ("2021-12-25", "Christmas 2021"),
        ("2022-02-28 23:59:59", "End of Feb 2022"),
    ]

    for ts, description in valid_dates:
        is_valid = validate_timestamp_schema(ts)
        expected = True  # All these should be valid
        test_cases.append({
            "timestamp": ts,
            "description": description,
            "is_valid": is_valid,
            "expected": expected,
            "passed": is_valid == expected
        })

    return test_cases


def run_all_tests(verbose: bool = False) -> Dict[str, Any]:
    """
    Run all timestamp validation tests.

    Args:
        verbose: If True, print detailed test results

    Returns:
        Dictionary with test results summary
    """
    all_tests = []

    # Run test suites
    print("Testing negative epoch timestamps (pre-1970)...")
    negative_tests = test_negative_epoch_timestamps()
    all_tests.extend(negative_tests)

    print("Testing pre-2020 dates...")
    pre_2020_tests = test_pre_2020_dates()
    all_tests.extend(pre_2020_tests)

    print("Testing valid post-2020 dates...")
    valid_tests = test_valid_post_2020_dates()
    all_tests.extend(valid_tests)

    # Calculate statistics
    total = len(all_tests)
    passed = sum(1 for test in all_tests if test["passed"])
    failed = total - passed

    # Print summary
    print("\nTest Results Summary:")
    print(f"  Total tests: {total}")
    print(f"  Passed: {passed}")
    print(f"  Failed: {failed}")

    # Print failed tests if any
    if failed > 0 and verbose:
        print("\nFailed tests:")
        for test in all_tests:
            if not test["passed"]:
                print(f"  - {test['description']}")
                print(f"    Timestamp: {test['timestamp']}")
                print(f"    Expected: {test['expected']}, Got: {test['is_valid']}")

    return {
        "total_tests": total,
        "passed": passed,
        "failed": failed,
        "all_tests": all_tests
    }


def main(argv: Optional[List[str]] = None) -> int:
    """
    Main entry point for the edge test.

    Args:
        argv: Command line arguments

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    parser = argparse.ArgumentParser(
        description="Edge test for negative timestamps and pre-2020 date validation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                    # Run all tests
  %(prog)s --verbose          # Run with detailed output
  %(prog)s --json             # Output results as JSON
  %(prog)s --validate-only    # Only validate schema function
        """
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print detailed test results"
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON"
    )

    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Only validate the schema function without running full tests"
    )

    parser.add_argument(
        "--timestamp",
        type=str,
        help="Validate a specific timestamp (supports int, float, or ISO string)"
    )

    args = parser.parse_args(argv)

    if args.validate_only:
        # Just validate that the schema function exists and is callable
        print("Schema validation function check:")
        print(f"  Function exists: {callable(validate_timestamp_schema)}")
        print(f"  Function signature: {validate_timestamp_schema.__doc__}")
        return 0

    if args.timestamp:
        # Validate a specific timestamp
        try:
            # Try to parse as number first
            try:
                ts = float(args.timestamp)
                if ts.is_integer():
                    ts = int(ts)
            except ValueError:
                ts = args.timestamp

            is_valid = validate_timestamp_schema(ts)
            print(f"Timestamp: {args.timestamp}")
            print(f"Valid: {is_valid}")

            if isinstance(ts, (int, float)):
                try:
                    dt = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
                    print(f"Date: {dt}")
                    print(f"Year: {dt.year}")
                except (ValueError, OverflowError, OSError):
                    print("Date: Invalid timestamp for platform")

            return 0 if is_valid else 1

        except Exception as e:
            print(f"Error validating timestamp: {e}")
            return 1

    # Run all tests
    results = run_all_tests(verbose=args.verbose)

    if args.json:
        # Output as JSON
        json_output = {
            "summary": {
                "total_tests": results["total_tests"],
                "passed": results["passed"],
                "failed": results["failed"]
            },
            "tests": results["all_tests"]
        }
        print(json.dumps(json_output, indent=2, default=str))

    # Return non-zero exit code if any tests failed
    return 1 if results["failed"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())

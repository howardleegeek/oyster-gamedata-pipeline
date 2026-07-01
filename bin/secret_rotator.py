#!/usr/bin/env python3
"""G132 · S3 Secret Rotator — 90-day automatic IAM access-key rotation with overlap window.
Zero-downtime: create secondary → wait overlap → deactivate primary → delete & promote."""
from __future__ import annotations
import argparse, datetime, json, logging, os, sys, tempfile
from pathlib import Path
from typing import Any, Dict, Optional, List
_boto3: Any = None; _yaml: Any = None
DEFAULT_ROTATION_DAYS, DEFAULT_OVERLAP_DAYS, DEFAULT_REGION = 90, 14, "us-east-1"
log = logging.getLogger("secret_rotator")
def _lazy_boto3() -> Any:
    global _boto3
    if _boto3 is None: import boto3 as _b; _boto3 = _b
    return _boto3
def _lazy_yaml() -> Any:
    global _yaml
    if _yaml is None: import yaml as _y; _yaml = _y
    return _yaml
class Config:
    """Rotation configuration from env, YAML, or CLI."""
    def __init__(self, aws_access_key_id: Optional[str] = None,
                 aws_secret_access_key: Optional[str] = None,
                 aws_region: str = DEFAULT_REGION, rotation_days: int = DEFAULT_ROTATION_DAYS,
                 overlap_days: int = DEFAULT_OVERLAP_DAYS,
                 iam_user: str = "secret-rotator-user",
                 state_file: Optional[Path] = None, dry_run: bool = False) -> None:
        (self.aws_access_key_id, self.aws_secret_access_key, self.aws_region,
         self.rotation_days, self.overlap_days, self.iam_user, self.dry_run) = (
            aws_access_key_id, aws_secret_access_key, aws_region,
            rotation_days, overlap_days, iam_user, dry_run)
        self.state_file = state_file or Path(os.path.expanduser("~"), ".secret_rotator", "state.json")
    @classmethod
    def from_env(cls, **kw: Any) -> "Config":
        """Create Config from environment variables.

        Reads AWS credentials and configuration from environment variables:
        - AWS_ACCESS_KEY_ID
        - AWS_SECRET_ACCESS_KEY
        - AWS_REGION (default: us-east-1)
        - ROTATION_DAYS (default: 90)
        - OVERLAP_DAYS (default: 14)
        - IAM_USER (default: secret-rotator-user)
        - STATE_FILE (optional)

        Args:
            **kw: Additional keyword arguments passed to Config constructor.

        Returns:
            Config: A new Config instance populated from environment variables.

        Example:
            >>> import os
            >>> os.environ["AWS_ACCESS_KEY_ID"] = "AKIAIOSFODNN7EXAMPLE"
            >>> os.environ["AWS_SECRET_ACCESS_KEY"] = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
            >>> config = Config.from_env()
        """
        return cls(aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
            aws_region=os.environ.get("AWS_REGION", DEFAULT_REGION),
            rotation_days=int(os.environ.get("ROTATION_DAYS", DEFAULT_ROTATION_DAYS)),
            overlap_days=int(os.environ.get("OVERLAP_DAYS", DEFAULT_OVERLAP_DAYS)),
            iam_user=os.environ.get("IAM_USER", "secret-rotator-user"),
            state_file=Path(os.environ["STATE_FILE"]) if os.environ.get("STATE_FILE") else None, **kw)
    @classmethod
    def from_yaml(cls, path: Path, **kw: Any) -> "Config":
        """Create Config from a YAML file.

        Reads AWS credentials and configuration from a YAML file.

        Args:
            path: Path to the YAML configuration file.
            **kw: Additional keyword arguments passed to Config constructor.

        Returns:
            Config: A new Config instance populated from the YAML file.

        Raises:
            FileNotFoundError: If the specified path does not exist.
        """
        y = _lazy_yaml()
        with open(path) as fh: data = y.safe_load(fh) or {}
        return cls(aws_access_key_id=data.get("aws_access_key_id"),
            aws_secret_access_key=data.get("aws_secret_access_key"),
            aws_region=data.get("aws_region", DEFAULT_REGION),
            rotation_days=int(data.get("rotation_days", DEFAULT_ROTATION_DAYS)),
            overlap_days=int(data.get("overlap_days", DEFAULT_OVERLAP_DAYS)),
            iam_user=data.get("iam_user", "secret-rotator-user"), **kw)
class RotationState:
    """Persisted JSON state tracking active/secondary keys and timestamps."""
    def __init__(self, state_file: Path) -> None:
        self.state_file = state_file; self.state: Dict[str, Any] = {}
        if state_file.exists():
            try:
                with open(state_file) as fh: self.state = json.load(fh)
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("Corrupt state %s: %s", state_file, exc)
    def save(self) -> None:
        """Save the rotation state to disk atomically.

        Writes to a temporary directory first, then atomically replaces the
        state file to avoid partial writes on crash.

        Raises:
            OSError: If the directory cannot be created or the file cannot be written.
        """
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = tempfile.mkdtemp(dir=self.state_file.parent)
        tmp = Path(tmp_dir, "tmp.json")
        with open(tmp, "w") as fh: json.dump(self.state, fh, indent=2, default=str)
        tmp.replace(self.state_file); tmp.parent.rmdir()
    @property
    def active_key_id(self) -> Optional[str]: return self.state.get("active_key_id")
    @property
    def secondary_key_id(self) -> Optional[str]: return self.state.get("secondary_key_id")
    @property
    def secondary_created(self) -> Optional[datetime.datetime]:
        raw = self.state.get("secondary_created_at")
        return datetime.datetime.fromisoformat(raw) if raw else None
    @property
    def primary_deactivated_at(self) -> Optional[datetime.datetime]:
        raw = self.state.get("primary_deactivated_at")
        return datetime.datetime.fromisoformat(raw) if raw else None
    def set_secondary(self, kid: str) -> None:
        self.state["secondary_key_id"] = kid
        self.state["secondary_created_at"] = datetime.datetime.utcnow().isoformat()
    def deactivate_primary(self) -> None:
        self.state["primary_deactivated_at"] = datetime.datetime.utcnow().isoformat()
    def promote_secondary(self) -> None:
        self.state["active_key_id"] = self.state.pop("secondary_key_id", None)
        self.state.pop("secondary_created_at", None); self.state.pop("primary_deactivated_at", None)
    def reset(self) -> None: self.state.clear()
class IAMRotator:
    """Thin boto3 IAM wrapper for key lifecycle."""
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        kw: Dict[str, Any] = {"region_name": cfg.aws_region}
        if cfg.aws_access_key_id: kw["aws_access_key_id"] = cfg.aws_access_key_id
        if cfg.aws_secret_access_key: kw["aws_secret_access_key"] = cfg.aws_secret_access_key
        self._iam = _lazy_boto3().client("iam", **kw)
    def create_access_key(self) -> Dict[str, Any]:
        return self._iam.create_access_key(UserName=self.cfg.iam_user)["AccessKey"]
    def deactivate_key(self, kid: str) -> None:
        self._iam.update_access_key(UserName=self.cfg.iam_user, AccessKeyId=kid, Status="Inactive")
    def delete_key(self, kid: str) -> None:
        self._iam.delete_access_key(UserName=self.cfg.iam_user, AccessKeyId=kid)
    def list_keys(self) -> List[Dict[str, Any]]:
        return self._iam.list_access_keys(UserName=self.cfg.iam_user).get("AccessKeyMetadata", [])
    def rotate(self, state: RotationState) -> None:
        """Execute one rotation step: create → wait → deactivate → delete & promote."""
        now = datetime.datetime.utcnow()
        keys = self.list_keys()
        log.info("Keys for %s: %d", self.cfg.iam_user, len(keys))
        if not state.secondary_key_id:
            if len(keys) >= 2: log.error("Already 2 keys."); sys.exit(1)
            if self.cfg.dry_run: log.info("[DRY-RUN] Would create key."); return
            nk = self.create_access_key(); state.set_secondary(nk["AccessKeyId"]); state.save()
            log.info("Created secondary %s", nk["AccessKeyId"]); return
        if state.secondary_created:
            end = state.secondary_created + datetime.timedelta(days=self.cfg.overlap_days)
            if now < end: log.info("Overlap active; %s remaining.", end - now); return
        if not state.primary_deactivated_at and state.active_key_id:
            if self.cfg.dry_run: log.info("[DRY-RUN] Would deactivate %s", state.active_key_id); return
            self.deactivate_key(state.active_key_id); state.deactivate_primary(); state.save()
            log.info("Deactivated %s", state.active_key_id); return
        del_after = (state.primary_deactivated_at + datetime.timedelta(days=self.cfg.overlap_days)
                     ) if state.primary_deactivated_at else None
        if del_after and now >= del_after:
            if self.cfg.dry_run: log.info("[DRY-RUN] Would delete & promote."); return
            if state.active_key_id: self.delete_key(state.active_key_id)
            state.promote_secondary(); state.save()
            log.info("Promoted secondary; old key deleted."); return
        log.info("Rotation in progress; next step at %s", del_after)
    def status(self, state: RotationState) -> None:
        """Print current key and rotation status."""
        keys = self.list_keys()
        print(f"IAM user : {self.cfg.iam_user}\nKeys     : {len(keys)}")
        for k in keys: print(f"  {k['AccessKeyId']}  {k['Status']}  created={k.get('CreateDate')}")
        print(f"Active   : {state.active_key_id or '(none)'}")
        print(f"Secondary: {state.secondary_key_id or '(none)'}")
        if state.secondary_created: print(f"Secondary created: {state.secondary_created}")
        if state.primary_deactivated_at: print(f"Primary deactivated: {state.primary_deactivated_at}")
    def init_state(self, state: RotationState) -> None:
        """Bootstrap state from existing IAM keys."""
        keys = self.list_keys()
        if not keys: state.reset(); state.save(); return
        active = next((k for k in keys if k["Status"] == "Active"), keys[0])
        state.state["active_key_id"] = active["AccessKeyId"]; state.save()
def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    p = argparse.ArgumentParser(prog="secret_rotator",
        description="90-day automatic IAM key rotation with overlap window.")
    p.add_argument("--config", type=Path); p.add_argument("--iam-user", default=None)
    p.add_argument("--region", default=None); p.add_argument("--rotation-days", type=int, default=None)
    p.add_argument("--overlap-days", type=int, default=None)
    p.add_argument("--state-file", type=Path, default=None)
    p.add_argument("--dry-run", action="store_true"); p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("rotate"); sub.add_parser("status"); sub.add_parser("init")
    return p
def main(argv: list[str] | None = None) -> int:
    """Entry point – parse args, build config, dispatch command."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s")
    cfg = Config.from_env(dry_run=args.dry_run, state_file=args.state_file)
    if args.config: cfg = Config.from_yaml(args.config, dry_run=args.dry_run, state_file=args.state_file)
    if args.iam_user: cfg.iam_user = args.iam_user
    if args.region: cfg.aws_region = args.region
    if args.rotation_days is not None: cfg.rotation_days = args.rotation_days
    if args.overlap_days is not None: cfg.overlap_days = args.overlap_days
    state = RotationState(cfg.state_file); rotator = IAMRotator(cfg)
    if args.command == "rotate": rotator.rotate(state)
    elif args.command == "status": rotator.status(state)
    elif args.command == "init": rotator.init_state(state)
    else: build_parser().print_help(); return 1
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
# -*- coding: utf-8 -*-
"""
library.py
Profile library: several named class profiles held at once.

A single active profile does not scale to a layer with a dozen or more classes:
the right file would have to be loaded by hand before every check, and one
mistake would silently score a feature against the wrong class. Here all the
profiles live in the project together, and the right one is picked by the
feature's class field value.
"""

import json

from .profile import ClassProfile

PROJECT_SCOPE = "calc_sp_ch"
PROJECT_ENTRY = "class_profiles"
LEGACY_ENTRY = "class_profile"      # single profile from early versions

FORMAT = "calc_sp_ch/class-library"
VERSION = 1


class ProfileLibrary:
    """{class name: ClassProfile}, persisted into the project."""

    def __init__(self, project):
        self.project = project
        self._profiles = {}
        self._active = None
        self.reload()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def reload(self):
        self._profiles, self._active = {}, None
        raw, ok = self.project.readEntry(PROJECT_SCOPE, PROJECT_ENTRY)
        if ok and raw:
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                data = None
            if isinstance(data, dict):
                for name, payload in (data.get("profiles") or {}).items():
                    try:
                        self._profiles[name] = ClassProfile.from_dict(payload)
                    except (ValueError, TypeError, KeyError):
                        continue
                self._active = data.get("active")

        if not self._profiles:
            self._migrate_legacy()

        if self._active not in self._profiles:
            self._active = next(iter(self._profiles), None)

    def _migrate_legacy(self):
        """An old-style single profile -> a library holding one profile."""
        raw, ok = self.project.readEntry(PROJECT_SCOPE, LEGACY_ENTRY)
        if not ok or not raw:
            return
        try:
            profile = ClassProfile.from_dict(json.loads(raw))
        except (ValueError, TypeError, KeyError):
            return
        self._profiles[profile.name] = profile
        self._active = profile.name

    def flush(self):
        if not self._profiles:
            self.project.removeEntry(PROJECT_SCOPE, PROJECT_ENTRY)
            return
        payload = {
            "version": VERSION,
            "active": self._active,
            "profiles": {name: profile.to_dict()
                         for name, profile in self._profiles.items()},
        }
        self.project.writeEntry(PROJECT_SCOPE, PROJECT_ENTRY,
                                json.dumps(payload, ensure_ascii=False))

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------

    def names(self):
        return sorted(self._profiles)

    def options(self):
        """[(display label, class name)] - labels taken from the layer legend."""
        return [(self._profiles[name].title, name) for name in self.names()]

    def title(self, name):
        profile = self.get(name)
        return profile.title if profile is not None else name

    def get(self, name):
        return self._profiles.get(name) if name else None

    def __len__(self):
        return len(self._profiles)

    def __contains__(self, name):
        return name in self._profiles

    def __bool__(self):
        return bool(self._profiles)

    @property
    def active_name(self):
        return self._active

    @property
    def active(self):
        return self.get(self._active)

    def set_active(self, name):
        if name in self._profiles:
            self._active = name
            self.flush()
        return self._active

    def add(self, profile, make_active=True):
        """Add or replace a profile (keyed by class name)."""
        self._profiles[profile.name] = profile
        if make_active or self._active is None:
            self._active = profile.name
        self.flush()
        return profile

    def remove(self, name):
        if name not in self._profiles:
            return False
        del self._profiles[name]
        if self._active == name:
            self._active = next(iter(self.names()), None)
        self.flush()
        return True

    def clear(self):
        self._profiles, self._active = {}, None
        self.flush()

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    def to_dict(self):
        return {
            "format": FORMAT,
            "version": VERSION,
            "active": self._active,
            "profiles": {name: profile.to_dict()
                         for name, profile in self._profiles.items()},
        }

    def save(self, path):
        """Save the whole library into a single file."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        return path

    def load(self, path, merge=True):
        """
        Load a library file OR a single-profile file; the format is detected
        from the "format" field. Returns the list of names loaded.
        """
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("this file does not look like a class profile")

        if data.get("format") == FORMAT or "profiles" in data:
            payload = data.get("profiles") or {}
            if not payload:
                raise ValueError("the file contains no profiles")
            if not merge:
                self._profiles, self._active = {}, None
            loaded = []
            for name, item in payload.items():
                try:
                    profile = ClassProfile.from_dict(item)
                except (ValueError, TypeError, KeyError):
                    continue
                self._profiles[profile.name] = profile
                loaded.append(profile.name)
            if not loaded:
                raise ValueError("not a single profile could be read")
            active = data.get("active")
            self._active = active if active in self._profiles else loaded[0]
            self.flush()
            return loaded

        profile = ClassProfile.from_dict(data)      # single profile
        if not merge:
            self._profiles, self._active = {}, None
        self.add(profile)
        return [profile.name]

    # ------------------------------------------------------------------

    def summary(self):
        """A short summary for the interface."""
        if not self._profiles:
            return "No class profiles"
        profile = self.active
        if profile is None:
            return f"Class profiles: {len(self._profiles)}"
        text = f"{profile.title}: {profile.n_pixels} px, {profile.k} bands"
        if profile.singular:
            text += " ⚠"
        if len(self._profiles) > 1:
            text += f" (total {len(self._profiles)})"
        return text

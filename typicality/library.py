# -*- coding: utf-8 -*-
"""
library.py
Библиотека эталонов: несколько именованных профилей классов одновременно.

Один активный эталон не годится, когда в слое десяток-другой классов: перед
каждой проверкой пришлось бы вручную подгружать нужный файл, и одна ошибка
означала бы молча посчитанную оценку против чужого класса. Здесь профили лежат
в проекте все сразу, а нужный выбирается по значению поля класса у объекта.
"""

import json

from .profile import ClassProfile

PROJECT_SCOPE = "calc_sp_ch"
PROJECT_ENTRY = "class_profiles"
LEGACY_ENTRY = "class_profile"      # одиночный эталон ранних версий

FORMAT = "calc_sp_ch/class-library"
VERSION = 1


class ProfileLibrary:
    """{имя класса: ClassProfile} с записью в проект."""

    def __init__(self, project):
        self.project = project
        self._profiles = {}
        self._active = None
        self.reload()

    # ------------------------------------------------------------------
    # Персистентность
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
        """Одиночный эталон старых версий → библиотека из одного профиля."""
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
    # Доступ
    # ------------------------------------------------------------------

    def names(self):
        return sorted(self._profiles)

    def options(self):
        """[(подпись для интерфейса, имя класса)] — подписи из легенды слоя."""
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
        """Добавить или заменить профиль (ключ — имя класса)."""
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
    # Файлы
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
        """Сохранить всю библиотеку в один файл."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        return path

    def load(self, path, merge=True):
        """
        Загрузить файл библиотеки ИЛИ одиночного профиля — формат определяется
        по полю "format". Возвращает список загруженных имён.
        """
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("файл не похож на эталон класса")

        if data.get("format") == FORMAT or "profiles" in data:
            payload = data.get("profiles") or {}
            if not payload:
                raise ValueError("в файле нет ни одного профиля")
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
                raise ValueError("не удалось прочитать ни одного профиля")
            active = data.get("active")
            self._active = active if active in self._profiles else loaded[0]
            self.flush()
            return loaded

        profile = ClassProfile.from_dict(data)      # одиночный профиль
        if not merge:
            self._profiles, self._active = {}, None
        self.add(profile)
        return [profile.name]

    # ------------------------------------------------------------------

    def summary(self):
        """Короткая сводка для интерфейса."""
        if not self._profiles:
            return "Эталонов нет"
        profile = self.active
        if profile is None:
            return f"Эталонов: {len(self._profiles)}"
        text = f"{profile.title}: {profile.n_pixels} пикс., {profile.k} кан."
        if profile.singular:
            text += " ⚠"
        if len(self._profiles) > 1:
            text += f" (всего {len(self._profiles)})"
        return text

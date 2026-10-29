"""Native desktop banners. macOS uses UserNotifications, so the banner carries the app's icon and an action button;
anything else (or a failure) falls back to a plain osascript notification, and the in-page popup is always shown too."""
from __future__ import annotations

import contextlib
import logging
import subprocess
import sys

log = logging.getLogger(__name__)
_keep: list = []  # the delegate must outlive the call
ACTION = "prismix.action"


def _fallback(title: str, body: str) -> None:
    if sys.platform != "darwin":
        return
    script = f"display notification {_q(body)} with title {_q(title)}"
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, timeout=10)


def _q(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def banner(title: str, body: str, *, action: str = "", on_action=None, ident: str = "prismix") -> None:
    """Show a banner. `action` adds a button (clicking it or the banner itself calls `on_action`). Never raises."""
    if sys.platform != "darwin":
        return
    try:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(_show, title, body, action, on_action, ident)
    except Exception:  # noqa: BLE001 - a banner is a courtesy
        log.info("native banner unavailable, using the fallback", exc_info=True)
        _fallback(title, body)


def _show(title: str, body: str, action: str, on_action, ident: str) -> None:
    try:
        from Foundation import NSObject
        from UserNotifications import (
            UNMutableNotificationContent,
            UNNotificationAction,
            UNNotificationActionOptionForeground,
            UNNotificationCategory,
            UNNotificationDefaultActionIdentifier,
            UNNotificationPresentationOptionBanner,
            UNNotificationPresentationOptionList,
            UNNotificationRequest,
            UNUserNotificationCenter,
        )

        center = UNUserNotificationCenter.currentNotificationCenter()

        class Delegate(NSObject):
            def userNotificationCenter_willPresentNotification_withCompletionHandler_(self, c, n, done):  # noqa: N802
                done(UNNotificationPresentationOptionBanner | UNNotificationPresentationOptionList)  # also while the app is in front

            def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(self, c, response, done):  # noqa: N802
                if on_action and response.actionIdentifier() in (ACTION, UNNotificationDefaultActionIdentifier):
                    on_action()
                done()

        delegate = Delegate.alloc().init()
        _keep[:] = [delegate]
        center.setDelegate_(delegate)
        category = ident
        if action:
            button = UNNotificationAction.actionWithIdentifier_title_options_(ACTION, action, UNNotificationActionOptionForeground)
            center.setNotificationCategories_({UNNotificationCategory.categoryWithIdentifier_actions_intentIdentifiers_options_(category, [button], [], 0)})
        content = UNMutableNotificationContent.alloc().init()
        content.setTitle_(title)
        content.setBody_(body)
        content.setCategoryIdentifier_(category)

        def send(granted, error):
            if not granted:
                log.info("notifications are not allowed for Prismix: %s", error)
                return
            center.addNotificationRequest_withCompletionHandler_(UNNotificationRequest.requestWithIdentifier_content_trigger_(ident, content, None), None)

        center.requestAuthorizationWithOptions_completionHandler_(0b111, send)  # badge, sound, alert
    except Exception:  # noqa: BLE001
        log.info("UserNotifications failed, using the fallback", exc_info=True)
        _fallback(title, body)

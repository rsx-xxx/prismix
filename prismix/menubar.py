"""macOS menu-bar mini player: a status item whose popover shows /mini (the Band remote controls of the running app)."""
from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)
_keep: list = []  # the status item, popover and handler must outlive install()
SIZE = (340, 470)


def install(url: str) -> None:
    """Add the status item. Safe to call from any thread; does nothing off macOS or when PyObjC is missing."""
    if sys.platform != "darwin":
        return
    try:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(_build, url)
    except Exception:  # noqa: BLE001 - the mini player is optional
        log.exception("menu-bar player unavailable")


def _build(url: str) -> None:
    try:
        from AppKit import (
            NSApp,
            NSImage,
            NSMakeRect,
            NSPopover,
            NSStatusBar,
            NSVariableStatusItemLength,
            NSViewController,
        )
        from Foundation import NSURL, NSObject, NSURLRequest
        from WebKit import WKWebView, WKWebViewConfiguration

        web = WKWebView.alloc().initWithFrame_configuration_(NSMakeRect(0, 0, *SIZE), WKWebViewConfiguration.alloc().init())
        web.setValue_forKey_(False, "drawsBackground")
        controller = NSViewController.alloc().init()
        controller.setView_(web)
        popover = NSPopover.alloc().init()
        popover.setBehavior_(1)  # transient: closes when the user clicks elsewhere
        popover.setContentSize_(SIZE)
        popover.setContentViewController_(controller)

        class Toggle(NSObject):
            def toggle_(self, sender):  # noqa: N802 - Objective-C selector
                if popover.isShown():
                    popover.performClose_(sender)
                    return
                web.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(f"{url}/mini")))
                popover.showRelativeToRect_ofView_preferredEdge_(sender.bounds(), sender, 1)  # 1 = below the bar
                NSApp.activateIgnoringOtherApps_(True)

        handler = Toggle.alloc().init()
        item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        button = item.button()
        icon = NSImage.imageWithSystemSymbolName_accessibilityDescription_("slider.vertical.3", "Prismix")
        if icon:
            icon.setTemplate_(True)
            button.setImage_(icon)
        else:
            button.setTitle_("Prismix")
        button.setTarget_(handler)
        button.setAction_(b"toggle:")
        _keep.extend([item, popover, handler, web, controller])
    except Exception:  # noqa: BLE001
        log.exception("menu-bar player could not start")

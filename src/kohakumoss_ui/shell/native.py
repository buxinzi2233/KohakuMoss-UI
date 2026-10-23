"""pywebview window over the same local HTTP app."""

def open_window(url: str, title: str = "KohakuMoss") -> int:
    """Block on the native window; must run on the main thread."""
    import webview

    webview.create_window(
        title,
        url,
        width=1440,
        height=920,
        min_size=(1080, 720),
        background_color="#efe9dc",
    )
    webview.start()
    return 0

# Upload fix notes (build V4 + upload hardening)

## Why uploads failed
1. **Locked file on Windows** - `pd.ExcelFile(...)` was opened in `data_processor.py` and `stock_engine.py`
   without being closed. Windows will not rename a file that is still open, so `os.replace(tmp, Enquiry.xlsx)`
   raised PermissionError *after* the upload had been saved and validated. The server answered with a plain-text
   500 and the page then failed parsing it as JSON. The `data/Enquiry.xlsx.uploading` file in the zip is exactly
   that leftover (it is a complete, valid workbook).
2. **Behind HTTPS (Render)** - the CSRF check compared `Origin` with `request.base_url`; behind the proxy the app
   sees `http://` while the browser sends `https://`, so every POST was rejected with "Invalid origin".
3. Fixed temp name, no cleanup, no rollback, blocking reload on the event loop, non-atomic reload.

## What changed
- data_processor.py / stock_engine.py: every workbook opened in a `with` block; `reload()` builds everything
  first and swaps it in under a lock (a failed reload keeps the old data).
- main.py: new `/api/upload` - unique temp file, size cap (MAX_UPLOAD_MB, default 50), validate first, backup +
  retry-on-lock replace, automatic rollback, always JSON errors, one upload at a time, stale `.uploading` / `.bak`
  cleaned or recovered at start-up. Also checks the Enquiry Date column is dd/mm/yyyy readable.
- auth.py: proxy-safe `same_origin()` (host based) used by login and every POST.
- static/js/app.js: tolerant response handling, 401 -> login, timeout, readable messages for 403/413/502/504.
- run.py / render.yaml: uvicorn `--proxy-headers --forwarded-allow-ips`.

## Render note
Render's free plan has an ephemeral disk: an uploaded workbook disappears on every redeploy/restart. Attach a
persistent disk (paid) and point DATA_DIR at it, or re-upload after each deploy.

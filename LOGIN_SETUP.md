# Unnati Hyundai — Screen access only

Full users have all existing dashboard screens and management actions.
Limited users have Enquiry, Enquiry Follow-up, Test Drive, Booking, Retails and Exchange, and may view ANY consultant on these six screens. No team assignment or consultant mapping is required. Other screens and management APIs are blocked server-side.

The included data/Data.xlsx retains the accounts supplied in your uploaded workbook. Column E is now Designation, for reference only; it does not filter data or determine access. Access is controlled by the Full/Limited value in column C. Existing workbooks with a Consultants column continue to work: that column is ignored.

Adding manpower: add a row in Users with User ID, Password, Access (Full or Limited), Name, optional Designation and Active. Save and close the workbook. New accounts are recognised on the next login without changing code. Keep IDs/passwords formatted as Text. Consultant dropdowns come from Enquiry.xlsx; new consultants appear when the enquiry data is updated through the existing Full-user upload/refresh process.

Extract the complete folder, install requirements with `python -m pip install -r requirements.txt`, then run `python run.py`. To upgrade, stop the old process and replace the application files. Keep your latest Enquiry.xlsx, Stock.xlsx and other operational workbooks if they are newer than the copies included here. Save your account workbook as data/Data.xlsx. Restart and sign in again.

The original data_processor.py is restored exactly. Dashboard HTML, CSS and chart/report computations are unchanged from the previously supplied login package. The six-screen menu restrictions remain.

Sessions expire after 8 hours and are held in memory; use one uvicorn worker. Restarting logs users out. Set COOKIE_SECURE=1 for HTTPS hosting. AUTH_DATA_FILE may specify another account workbook path. Old DASHBOARD_USER/DASHBOARD_PASSWORD variables are unused. Keep account workbooks private.

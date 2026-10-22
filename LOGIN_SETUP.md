# Unnati Hyundai — Screen access only

Full users have all existing dashboard screens and management actions.
Limited users have Enquiry, Enquiry Follow-up, Test Drive, Booking, Retails and Exchange. Other screens (including the Daily Report) and management APIs are blocked server-side. A Limited user whose Designation is **Team Leader** sees ONLY their own team's data on those six screens (see "Team Leaders" below); Limited users with any other designation still see every consultant.

The included data/Data.xlsx retains the accounts supplied in your uploaded workbook. Column E is Designation. Access is controlled by the Full/Limited value in column C; Designation only matters for the Team Leader rule below.

Adding manpower: add a row in Users with User ID, Password, Access (Full or Limited), Name, optional Designation and Active. Save and close the workbook. New accounts are recognised on the next login without changing code. Keep IDs/passwords formatted as Text. Consultant dropdowns come from Enquiry.xlsx; new consultants appear when the enquiry data is updated through the existing Full-user upload/refresh process.

Extract the complete folder, install requirements with `python -m pip install -r requirements.txt`, then run `python run.py`. To upgrade, stop the old process and replace the application files. Keep your latest Enquiry.xlsx, Stock.xlsx and other operational workbooks if they are newer than the copies included here. Save your account workbook as data/Data.xlsx. Restart and sign in again.

The original data_processor.py is restored exactly. Dashboard HTML, CSS and chart/report computations are unchanged from the previously supplied login package. The six-screen menu restrictions remain.

Sessions expire after 8 hours and are held in memory; use one uvicorn worker. Restarting logs users out. Set COOKIE_SECURE=1 for HTTPS hosting. AUTH_DATA_FILE may specify another account workbook path. Old DASHBOARD_USER/DASHBOARD_PASSWORD variables are unused. Keep account workbooks private.

## Team Leaders: each sees only their own team

Rule: Access = Limited **and** Designation contains "Team Leader" -> every figure, chart, list and dropdown on the six screens is limited to that Team Leader's consultants. It is enforced on the server for every request, so changing the Consultant dropdown or editing a URL cannot widen it.

Which consultants are a Team Leader's team? The Enquiry export has no Team Leader column, so list them in the **Consultants** column of the Users sheet (column G of the included Data.xlsx):

* Write each consultant exactly as the name appears in the Enquiry sheet (Consultant Name). Capitals and extra spaces do not matter; spelling does.
* Separate names with semicolons (commas or new lines also work), e.g. `Akshay Anand Goure; Pawan Bharat Borkar`.
* The Team Leader's own name is always included too, in case they handle enquiries themselves.
* Leave the column blank for Full users (it is ignored for them).

Safe default: a Team Leader with no consultants listed sees **no data** and a notice asking them to contact the admin. Names that do not match any consultant in the enquiry data are listed in the tooltip of the "Team view" badge at the top of the page, so typos are easy to find.

Editing the Consultants cell signs that Team Leader out and applies the new team at their next login. The included Data.xlsx has the column added but empty: **fill it in before Team Leaders use the dashboard.**

## Daily Report (Full access only)

A "Daily Report" screen after Overview, for Full-access users. It is not available to Team Leaders or any Limited user (the menu entry is hidden and `/api/daily*` returns 403).

It shows one chosen day and the month to date (Month, Day, Model, Consultant, Source and Sub-source filters): KPI cards; a consultant scorecard; customer records for Enquiries / Test drives / Bookings / Retails / Booking cancels with a CSV export; Consultant x Model tables; per-day charts; and a day-by-day table (click a row, a chart point or a bar to open that day). The figures use the same rules as the Enquiry, Test Drive, Booking and Retails screens, so they always tie to them; a booked/retailed/cancelled record with no date cannot be placed on a day and is reported in a notice instead.

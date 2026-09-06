# Connect Twitter/X during Web Setup

Start the server and follow [Setup](web-app.md#start-and-stop-the-app). Import your
archive first, then use this guide at **Connect Twitter/X** while the local import runs.
Paste the values into Settings → Setup; credentials are saved only after a
successful test. You may explicitly skip Twitter/X for offline browsing.

This page contains instructions on how to obtain your Twitter/X authentication cookies and numeric account ID.

> [!CAUTION]
> These cookies provide access to your logged-in Twitter/X session. Treat them like a password.

<details>
<summary><strong>Chrome / Chromium / Brave / Microsoft Edge / Opera / Helium</strong></summary>

### 1. Open Twitter/X

Go to:

```text
https://x.com/
```

Make sure you are logged in.

### 2. Open Developer Tools

Use one of the following:

**Windows / Linux**

```text
Ctrl + Shift + I
```

**macOS**

```text
Cmd + Option + I
```

You can also right-click anywhere on the page and choose **Inspect**.

### 3. Open the Application tab

In Developer Tools, select:

```text
Application
```

If you do not see it, click the `»` menu at the top of Developer Tools and choose **Application**.

### 4. Open Twitter/X cookies

In the left sidebar, expand:

```text
Storage
└── Cookies
```

Then select:

```text
https://x.com
```

Depending on your browser/session, you may also see cookies associated with:

```text
https://twitter.com
```

### 5. Find `auth_token`

Find the row whose **Name** is:

```text
auth_token
```

Copy the contents of its **Value** field.

### 6. Find `ct0`

Find the row whose **Name** is:

```text
ct0
```

Copy the contents of its **Value** field.

### 7. Find your numeric account ID

Find the row whose **Name** is:

```text
twid
```

Copy the numeric account ID from its **Value** field.

For example:

```text
u%3D123456789
```

gives the account ID:

```text
123456789
```

Use the digits only, not your `@username`.

</details>

<details>
<summary><strong>Firefox</strong></summary>

### 1. Open Twitter/X

Go to:

```text
https://x.com/
```

Make sure you are logged in.

### 2. Open Developer Tools

**Windows / Linux**

```text
Ctrl + Shift + I
```

**macOS**

```text
Cmd + Option + I
```

You can also right-click the page and choose **Inspect**.

### 3. Open the Storage tab

Select:

```text
Storage
```

If the tab is hidden, use the `»` menu in Developer Tools.

### 4. Open Twitter/X cookies

In the left sidebar, expand:

```text
Cookies
```

Then select:

```text
https://x.com
```

You may also see relevant cookies under:

```text
https://twitter.com
```

### 5. Find `auth_token`

Use the filter box and search for:

```text
auth_token
```

Select the matching cookie and copy its **Value**.

### 6. Find `ct0`

Search for:

```text
ct0
```

Select the matching cookie and copy its **Value**.

### 7. Find your numeric account ID

Find the cookie named:

```text
twid
```

Copy the numeric account ID from its **Value**.

For example:

```text
u%3D123456789
```

gives the account ID:

```text
123456789
```

Use the digits only, not your `@username`.

</details>

<details>
<summary><strong>Safari</strong></summary>

Safari's developer tools may need to be enabled first.

### 1. Enable Safari developer features

Open:

```text
Safari → Settings
```

Depending on your Safari version, look under **Advanced** and enable the option to show developer features / the Develop menu.

After enabling it, a **Develop** menu should appear in Safari's menu bar.

### 2. Open Twitter/X

Go to:

```text
https://x.com/
```

Make sure you are logged in.

### 3. Open Web Inspector

Use:

```text
Develop → Show Web Inspector
```

or the keyboard shortcut:

```text
Cmd + Option + I
```

### 4. Open the Storage section

In Web Inspector, select:

```text
Storage
```

Then locate the cookies associated with:

```text
x.com
```

Depending on your session, you may also find relevant entries associated with:

```text
twitter.com
```

### 5. Find `auth_token`

Locate the cookie named:

```text
auth_token
```

Copy its **Value**.

### 6. Find `ct0`

Locate the cookie named:

```text
ct0
```

Copy its **Value**.

### 7. Find your numeric account ID

Locate the cookie named:

```text
twid
```

Copy the numeric account ID from its **Value**.

For example:

```text
u%3D123456789
```

gives the account ID:

```text
123456789
```

Use the digits only, not your `@username`.

</details>

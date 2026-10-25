# Getting started

## Installation

The one-step installer supports macOS and Linux and can install the Python version it needs.

```bash
bash -c "$(curl -fsSL https://github.com/gezerwezer/tweetnook/releases/latest/download/install.sh)"
```

It asks how you'd like TweetNook to start, who can access it (this computer only or other devices on your network), then lets you choose add-ons. Automated tagging is optional and can be added later. The migration add-on is only needed if you're bringing over an older TweetNook archive.

On Linux with systemd, the first choice installs a background service and asks for administrator access. The service starts after installation and at boot. On macOS and systems without systemd, start TweetNook yourself:

```bash
tweetnook serve
```

Keep that terminal open while you use it. To run a personal copy in the background instead, use `tweetnook web start`; [maintenance](maintenance.md) covers stopping and restarting each startup mode.

You can choose the install mode without prompts:

```bash
bash -c "$(curl -fsSL https://github.com/gezerwezer/tweetnook/releases/latest/download/install.sh)" -- --no-service
# Linux with systemd:
bash -c "$(curl -fsSL https://github.com/gezerwezer/tweetnook/releases/latest/download/install.sh)" -- --service --automated-tagging
```

The server runs on macOS or Linux; pip does not add native Windows support. You can browse it from other operating systems. For a Python environment you already manage, install with `python -m pip install tweetnook`. Python 3.12 or newer is required. Use `python -m pip install 'tweetnook[automated-tagging]'` if you want the tagging add-on.

## Command-line setup

You can import, sync and search without starting the web app. After installing:

1. Find your configuration directory in [file locations](maintenance.md#file-locations). Create it and a private `config.toml` if they don't exist. Copy the X values using the [browser instructions](#connect-x), then save them under `[auth]`:

   ```toml
   [auth]
   auth_token = "YOUR_AUTH_TOKEN"
   ct0 = "YOUR_CT0"
   user_id = "YOUR_NUMERIC_USER_ID"
   ```

   Replace the placeholders and keep the file private. Direct Python/pip commands also accept the [credential environment variables](maintenance.md#credentials).
2. Check the connection with `tweetnook auth check`.
3. Run `tweetnook sync`, or use `tweetnook import x-archive /path/to/twitter-archive.zip` to bring in an official export. To import local files without credentials or network follow-up, use `--offline` and skip the connection check.
4. Use `tweetnook stats`, `tweetnook view all`, or `tweetnook search 'coffee'` to inspect what was saved.

The installer's global Linux command uses the service account and its configuration. Direct Python/pip commands use the current account's paths and environment. Keep those consistent throughout the recipe.

Scheduled jobs require the web server to stay running. Finishing a command-line sync does not start a scheduler.

## Setup

Open the address TweetNook prints, then:

1. Import the ZIP you downloaded from X, migrate an older archive, or start with an empty archive. Keep the original ZIP somewhere safe. Imports can continue while you work through Setup.
2. Connect your X account using the cookies below. You can explicitly skip this step for an archive you only want to read.
3. Choose which collections to sync: bookmarks, likes, and your own tweets. Bookmarks and likes are selected by default. Set a schedule if you'd like automatic syncs.
4. Choose your appearance settings.
5. Create and confirm a web password, or explicitly choose to use TweetNook without one. A password is recommended when other devices can reach it.

An archive import fills in available post details and downloads media as it runs. Once it finishes and Setup is complete with X connected, TweetNook starts the first sync using your collection choices. You'll see progress in Jobs. You can return to Settings > Setup to update the connection or run Setup again.

## Connect X

> [!CAUTION]
> Your X session cookies act like a password. `auth_token` and `ct0` give access to your X session. Keep them private, and never include them in screenshots, issue reports, or shared configuration files.

<details>
<summary><strong>Chrome / Chromium / Helium / Microsoft Edge / Opera / Brave</strong></summary>

### 1. Open Twitter/X

Go to:

```text
https://x.com/
```

Make sure you are logged in.

### 2. Open developer tools

Press the shortcut for your computer:

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

If it is hidden, open the `»` menu at the top of Developer Tools and choose **Application**.

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
Use only the digits, leaving off `u%3D` or `u=`.
For example:

```text
u%3D123456789
```

gives the account ID:

```text
123456789
```

### 8. Save the connection

Paste `auth_token` and `ct0` into TweetNook's Twitter/X connection step. Paste the digits from `twid` into User ID, or keep the correct ID if your imported archive has filled it in. Likes and Your tweets need this ID. Test the connection before continuing.

</details>

<details>
<summary><strong>Firefox</strong></summary>

### 1. Open Twitter/X

Go to:

```text
https://x.com/
```

Make sure you are logged in.

### 2. Open developer tools

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
Use only the digits, leaving off `u%3D` or `u=`.
For example:

```text
u%3D123456789
```

gives the account ID:

```text
123456789
```

### 8. Save the connection

Paste `auth_token` and `ct0` into TweetNook's Twitter/X connection step. Paste the digits from `twid` into User ID, or keep the correct ID if your imported archive has filled it in. Likes and Your tweets need this ID. Test the connection before continuing.

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
Use only the digits, leaving off `u%3D` or `u=`.
For example:

```text
u%3D123456789
```

gives the account ID:

```text
123456789
```

### 8. Save the connection

Paste `auth_token` and `ct0` into TweetNook's Twitter/X connection step. Paste the digits from `twid` into User ID, or keep the correct ID if your imported archive has filled it in. Likes and Your tweets need this ID. Test the connection before continuing.

</details>

Next, [browse your archive](web-app.md), [import older history](importing.md), or [choose a sync schedule](syncing.md).

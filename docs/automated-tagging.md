# Automated tagging

Automated tagging adds suggested tags and short descriptions to saved posts. It can help you find an image or video by what it shows, even when the original post has little text.

This is an optional add-on. It sends selected text, surrounding context, tagging hints, and media being tagged to Google's Gemini service, so decide whether you're comfortable with that before turning it on. Manual tags and the rest of TweetNook work without it.

## Setup

Choose Automated tagging when running the installer, or rerun the installer to add it to an existing installation. For an environment you manage yourself:

```bash
python -m pip install 'tweetnook[automated-tagging]'
```

Restart TweetNook after adding the package. Open Settings > Automated tagging, enter your Gemini API key, and choose Free or Paid usage. Refresh models to see the choices available to that key, then select a model and thinking level. A model appearing in the list doesn't promise that every media type or processing option will work with it; check Google's model documentation for the combination you want.

Save the settings and enable tagging. Normal syncs then tag eligible posts. You can also start Automated tags from Jobs or run `tweetnook tag`.

## Usage modes

| Mode | What to expect |
| --- | --- |
| Free | Groups similar kinds of posts into requests of up to twenty posts. Set the batch size and request limits to suit your account. Google Search is unavailable in this mode. |
| Paid | Tags one post per request. Choose Standard or Flex processing, and set a spending limit or explicitly allow unlimited spending. Google Search is optional. |

The default Free limits are ten requests per minute and twenty per day. These are TweetNook's settings, not a guarantee of the quota Google gives your account. The Free daily allowance resets at midnight in the Pacific timezone and is counted separately for each model. Retries and split batches also use allowance, and failures can remain counted.

Paid spending controls are local estimates and depend on the usage information available. Other applications using the same Google project aren't included. A budget in TweetNook isn't a billing cap at Google; check your provider's billing controls too.

## Usage limits

For Paid requests with missing billing information, choose whether TweetNook should stop immediately or reserve an estimated amount and continue within your daily budget. You can set a minimum reserve per uncertain request and a maximum number of uncertain requests per UTC day.

Google Search has separate uncertainty controls. You can reserve estimated usage or disable Search for the run. The local Search counter only includes requests made by TweetNook. A safety reserve stops Search before the configured free allowance is used, and the counter rolls over monthly.

## Test a post

In the Test run section, enter the ID of a post already in your archive. The preview uses the settings currently displayed, including unsaved changes. Review its tags, description, and run details before saving your settings.

A preview doesn't change the post's saved tags or description. It still makes Gemini requests and uses any applicable quota, paid spend, or Search allowance. Retries can add requests.

From a terminal:

```bash
tweetnook tag 123456789 --test
tweetnook tag --limit 5
tweetnook tag --batch 10
```

`--limit` bounds the main request loop, rather than the number of posts or every provider call. Retries and split batches can add calls. `--batch` changes the Free batch size for that invocation. `--model` lets you override the saved model for one run.

## Tagging context

Tagging context can list subjects you often save. These are hints, so a post still needs evidence for the suggested tag. Additional instructions let you express preferences about wording and subjects; TweetNook's built-in rules still apply.

Use `tag:` to search generated labels and `description:` to search descriptions. Suggested tags can be edited manually. If a run reaches its quota or budget, leave the saved work in place and continue later.

## What gets tagged

Normal runs work through newer eligible posts and their directly quoted originals. A post needs usable archived details, and media must already be downloaded. Run Download media first when needed; the default maximum is 100 MiB per media item.

The model is asked for two to five specific tags. Media posts can also get a description and a transcription of prominent text; text-only posts get tags without a generated description. Normal runs skip completed, nonempty tags. Explicitly targeting a post can replace its existing values, so preview first if you've edited them yourself.

Free batching can make the allowance go further, but context from one post may influence another in the same batch. Lower the batch size, down to one, if that affects your results. Common tags from your archive can also help the model reuse names consistently.

## Usage history

The Paid settings panel's Day, Week, Month and Year choices convert your entered amount into a daily allowance. They don't create separate rolling budgets. Paid daily accounting and chart dates use UTC.

View spend history shows estimates, requests, models, media types and observed Search usage. Choose 7 days, 30 days, 3 months, 1 year or Lifetime. Missing usage or prices can leave gaps. These estimates exclude Search/tool fees, account charges and requests from other applications; Google's billing records are the source for actual charges.

TweetNook gets pricing metadata from OpenRouter. Tagging requests go to Google. Uploaded media is cleaned up after requests on a best-effort basis and can remain subject to Google's file-retention rules if cleanup doesn't finish. Check Google's [models](https://ai.google.dev/gemini-api/docs/models), [pricing](https://ai.google.dev/gemini-api/docs/pricing), [Search](https://ai.google.dev/gemini-api/docs/google-search), and [Files](https://ai.google.dev/gemini-api/docs/files) documentation for provider-side terms and capabilities.

## Cost example

Use these figures as a ballpark estimate. They come from successful requests in one project instance using `gemini-3.6-flash`, Flex processing and High thinking. Your collection and current provider rates can produce different costs.

| Post type | Requests measured | Average per post | Estimate for 1,000 similar posts |
| --- | ---: | ---: | ---: |
| Text | 35 | $0.00157 | $1.57 |
| Images | 213 | $0.00312 | $3.12 |
| Videos | 105 | $0.00411 | $4.11 |
| GIFs | 18 | $0.00276 | $2.76 |
| Mixed media | 11 | $0.00355 | $3.55 |

Media length, model, processing options and retries affect your own results. Check the provider's rates before choosing Paid mode.

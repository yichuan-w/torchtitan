# Marketing Attribution Pipeline

You have two CSV files in `/app/data/`:

- `pageviews.csv`: Raw pageview events with columns `user_id`, `event_time`, `url`, `utm_source`, `utm_medium`, `utm_campaign`
- `conversions.csv`: Conversion events with columns `user_id`, `converted_at`, `revenue`

Build a DuckDB SQL pipeline that transforms this raw event data into attributed marketing touchpoints.

## Requirements

**Sessionization**: Group pageviews into sessions using a 30-minute inactivity timeout. A new session starts when more than 30 minutes pass between consecutive pageviews for the same user. When a session contains pageviews with different UTM parameters, use the UTM values from the first pageview of the session.

**Attribution window**: Only sessions occurring within 30 days before a conversion should be attributed to that conversion.

**Multi-conversion handling**: Users may have multiple conversions. Each conversion should be numbered sequentially per user (starting at 1), and sessions should only be attributed to conversions that occur after them.

**Attribution models**: For each session-to-conversion relationship, calculate attribution points using these models:
- First-touch: All credit goes to the earliest session in the path
- Last-touch: All credit goes to the session immediately before conversion
- Positional (40/20/40): 40% to first touch, 40% to last touch, remaining 20% divided equally among middle touches. Edge cases: single session gets 100%, two sessions split 50/50.
- Linear: Credit divided equally among all sessions
- Time-decay: Credit weighted by recency using exponential decay with a 7-day half-life. The formula is: `weight = 2^(-days_from_conversion / 7)`. Normalize weights so they sum to 1.0.

**Data quality**: Handle missing UTM parameters by using `'unknown'` as the default value for any null or empty UTM field.

**Outputs**: Generate two CSV files:

1. `/app/output/attribution.csv` - Session-level attribution with columns:
   - `user_id`, `conversion_id` (format: `user_id_#`), `session_id` (format: `user_id_s#`)
   - `session_start`, `utm_source`, `utm_medium`, `utm_campaign`
   - `total_sessions`, `session_position`
   - `first_touch_points`, `last_touch_points`, `positional_points`, `linear_points`, `time_decay_points`
   - `revenue`, `first_touch_revenue`, `last_touch_revenue`, `positional_revenue`, `linear_revenue`, `time_decay_revenue`
   
2. `/app/output/channel_summary.csv` - Aggregated campaign performance with columns:
   - `utm_source`, `utm_medium`, `utm_campaign`
   - `total_sessions`, `total_conversions_attributed`
   - `first_touch_revenue`, `last_touch_revenue`, `positional_revenue`, `linear_revenue`, `time_decay_revenue`
   - `first_touch_roas`, `last_touch_roas`, `positional_roas`, `linear_roas`, `time_decay_roas` (ROAS = attributed_revenue / total_sessions as a simple efficiency metric)

Round all numeric fields to 4 decimal places. Sort attribution.csv by user_id, conversion_id, session_position. Sort channel_summary.csv by time_decay_revenue descending.

# Core Metrics Definitions

## DAU (Daily Active Users)
- Definition: Count of distinct user_ids with at least one `session_start` event on a given date
- Source table: `user_daily_activity` (is_active = 1) or `events` (event_type = 'session_start')
- Note: A user must have a session to count. Page views alone don't count.

## New Users
- Definition: Users where `signup_date` = the reporting date
- Source: `users` table

## Revenue
- Sum of `revenue_usd` from `user_daily_activity` table
- Includes both subscription and one-time purchases
- Currency: always USD

## Chat Sessions  
- Count of events where event_type = 'chat_start'
- Source: `events` table

## D1 Retention
- Users active on day 1 after signup / Users who signed up on day 0
- Use `user_daily_activity` for this: JOIN users on signup_date, check is_active on signup_date + 1

## Common Dimensions for Drill-Down
- **country**: User's country from `users` table. Top markets: US, GB, DE, JP, BR, IN
- **platform**: ios, android, web — from `users` table
- **subscription_tier**: free, basic, premium — from `users` table
- **age_group**: from `users` table

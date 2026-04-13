# Table Reference Guide

## users
Primary user dimension table. One row per user.
- Key columns: user_id, signup_date, country, platform, subscription_tier, age_group
- Use this for JOINs to get user attributes

## events
Raw event stream. One row per event.
- Key columns: event_id, user_id, event_date, event_type, session_duration_sec
- Event types: session_start, page_view, feature_use, chat_start, purchase, subscription_change
- For session duration, only look at event_type = 'session_start'

## daily_metrics
Pre-aggregated daily KPIs. One row per date.
- Key columns: date, dau, new_users, revenue_usd, chat_sessions, avg_session_sec
- Good for quick trend checks before detailed investigation

## user_daily_activity
Pre-aggregated per-user-per-day metrics. 
- Key columns: user_id, date, is_active, has_chat, session_count, revenue_usd
- JOIN with `users` for dimension drill-down
- This is the main workhorse table for analysis

## experiments
AB experiment assignments. One row per user per experiment.
- Key columns: user_id, experiment_name, variant, first_exposure_date
- Current experiment: "new_onboarding_flow" (control, test_a, test_b)

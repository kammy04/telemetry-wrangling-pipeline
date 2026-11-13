-- Run with:  sqlite3 outputs/telemetry.db < sql/example_queries.sql

.headers on
.mode column

-- 1) Systems that missed the 95% uptime target on any day
SELECT date, system_id, manager_name, uptime_pct, hours_down, errors
FROM kpis_by_system
WHERE uptime_pct < 95
ORDER BY uptime_pct;

-- 2) Manager scorecard across the whole period
SELECT manager_name,
       ROUND(AVG(uptime_avg), 2) AS avg_uptime_pct,
       SUM(total_errors)        AS errors,
       SUM(hours_down)          AS hours_down
FROM kpis_by_manager
GROUP BY manager_name
ORDER BY avg_uptime_pct;

-- 3) Most common error codes
SELECT error_code, COUNT(*) AS occurrences
FROM clean_logs
WHERE error_code <> ''
GROUP BY error_code
ORDER BY occurrences DESC;

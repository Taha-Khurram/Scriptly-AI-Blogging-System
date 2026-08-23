"""Publish-time recommendations derived from Google Analytics hourly traffic."""
from app.core.logging import get_logger

logger = get_logger(__name__)

DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


class PublishTimeAgent:

    def get_best_times(self, creds, property_id):
        try:
            rows = self._fetch_hourly_traffic(creds, property_id)
            if not rows:
                return {"success": True, "suggestions": [], "reason": "no_data"}

            grid = self._aggregate_by_hour_and_day(rows)

            total_sessions = sum(cell["sessions"] for cell in grid.values())
            if total_sessions < 50:
                return {"success": True, "suggestions": [], "reason": "insufficient_data"}

            ranked = self._rank_time_slots(grid)
            suggestions = self._build_suggestions(ranked, grid, total_sessions)

            return {
                "success": True,
                "suggestions": suggestions,
                "data_period": "Last 28 days"
            }
        except ImportError as e:
            logger.exception("Analytics error")
            return {"success": False, "suggestions": [], "reason": "module_not_installed"}
        except Exception as e:
            logger.exception("PublishTimeAgent error")
            return {"success": False, "suggestions": [], "reason": "error"}

    def _fetch_hourly_traffic(self, creds, property_id):
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.analytics.data_v1beta.types import (
            RunReportRequest, DateRange, Metric, Dimension
        )
        client = BetaAnalyticsDataClient(credentials=creds)
        response = client.run_report(
            RunReportRequest(
                property=property_id,
                date_ranges=[DateRange(start_date="28daysAgo", end_date="yesterday")],
                dimensions=[
                    Dimension(name="hour"),
                    Dimension(name="dayOfWeek")
                ],
                metrics=[
                    Metric(name="sessions"),
                    Metric(name="screenPageViews"),
                    Metric(name="activeUsers")
                ],
                limit=10000
            )
        )
        return response.rows if response.rows else []

    def _aggregate_by_hour_and_day(self, rows):
        grid = {}
        counts = {}

        for row in rows:
            hour = int(row.dimension_values[0].value)
            day = int(row.dimension_values[1].value)
            sessions = int(row.metric_values[0].value)
            pageviews = int(row.metric_values[1].value)
            users = int(row.metric_values[2].value)

            key = (day, hour)
            if key not in grid:
                grid[key] = {"sessions": 0, "pageviews": 0, "users": 0}
                counts[key] = 0

            grid[key]["sessions"] += sessions
            grid[key]["pageviews"] += pageviews
            grid[key]["users"] += users
            counts[key] += 1

        for key in grid:
            c = max(counts[key], 1)
            grid[key]["sessions"] /= c
            grid[key]["pageviews"] /= c
            grid[key]["users"] /= c

        return grid

    def _rank_time_slots(self, grid):
        max_sessions = max((c["sessions"] for c in grid.values()), default=1) or 1
        max_pageviews = max((c["pageviews"] for c in grid.values()), default=1) or 1
        max_users = max((c["users"] for c in grid.values()), default=1) or 1

        scored = []
        for (day, hour), cell in grid.items():
            score = (
                0.5 * (cell["sessions"] / max_sessions) +
                0.3 * (cell["pageviews"] / max_pageviews) +
                0.2 * (cell["users"] / max_users)
            )
            scored.append((day, hour, score))

        scored.sort(key=lambda x: x[2], reverse=True)
        return scored

    def _build_suggestions(self, ranked, grid, total_sessions):
        suggestions = []
        used = set()
        global_avg = total_sessions / max(len(grid), 1)

        for day_idx, hour, score in ranked:
            if len(suggestions) >= 3:
                break
            if (day_idx, hour) in used:
                continue

            for adj in range(max(0, hour - 1), min(24, hour + 2)):
                used.add((day_idx, adj))

            period = "AM" if hour < 12 else "PM"
            display_hour = hour % 12 or 12
            display = f"{DAY_NAMES[day_idx]}, {display_hour}:00 {period}"

            cell_avg = grid[(day_idx, hour)]["sessions"]
            pct_above = round(((cell_avg - global_avg) / global_avg) * 100) if global_avg > 0 else 0

            reasoning = f"{DAY_NAMES[day_idx]}s at {display_hour}:00 {period} see {pct_above}% more traffic than average"

            suggestions.append({
                "day": DAY_NAMES[day_idx],
                "day_index": day_idx,
                "hour": hour,
                "display_time": display,
                "score": round(score, 3),
                "reasoning": reasoning
            })

        return suggestions

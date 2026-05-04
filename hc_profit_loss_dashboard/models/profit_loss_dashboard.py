import json
from collections import defaultdict
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_date


class ProfitLossDashboardWizard(models.TransientModel):
    _name = "hc.profit.loss.dashboard.wizard"
    _description = "Profit & Loss Dashboard Wizard"

    name = fields.Char(default="Profit & Loss Dashboard", readonly=True)
    options_json = fields.Text()

    _SECTION_BY_ACCOUNT_TYPE = {
        "income": "income",
        "income_other": "other_income",
        "expense_direct_cost": "cogs",
        "expense": "expenses",
        "expense_depreciation": "expenses",
    }
    _ACCOUNT_TYPES = tuple(_SECTION_BY_ACCOUNT_TYPE)
    _SUMMARY_ROWS = ("gross_profit", "operating_profit", "net_profit")

    @api.model
    def get_dashboard_data(self, options=None):
        normalized_options = self._normalize_options(options or {})
        return self._with_dashboard_context(normalized_options)._build_dashboard_payload(normalized_options)

    @api.model
    def action_export_pdf(self, options=None):
        normalized_options = self._normalize_options(options or {})
        wizard = self.with_context(
            allowed_company_ids=normalized_options["company_ids"]
        ).create({
            "options_json": json.dumps(self._serialize_options(normalized_options)),
        })
        return self.env.ref(
            "hc_profit_loss_dashboard.action_profit_loss_dashboard_pdf"
        ).with_context(
            allowed_company_ids=normalized_options["company_ids"]
        ).report_action(
            wizard,
            data={"options": self._serialize_options(normalized_options)},
        )

    @api.model
    def action_export_xlsx(self, options=None):
        normalized_options = self._normalize_options(options or {})
        wizard = self.with_context(
            allowed_company_ids=normalized_options["company_ids"]
        ).create({
            "options_json": json.dumps(self._serialize_options(normalized_options)),
        })
        return self.env.ref(
            "hc_profit_loss_dashboard.action_profit_loss_dashboard_xlsx"
        ).with_context(
            allowed_company_ids=normalized_options["company_ids"]
        ).report_action(
            wizard,
            data={"options": self._serialize_options(normalized_options)},
        )

    @api.model
    def action_open_move_lines(self, options=None, account_ids=None, section_key=None):
        normalized_options = self._normalize_options(options or {})
        dashboard = self._with_dashboard_context(normalized_options)
        domain = dashboard._get_base_domain(normalized_options)
        account_ids = self._ordered_unique_ids(account_ids or [])
        if account_ids:
            domain.append(("account_id", "in", account_ids))
            action_name = _("Journal Items")
        elif section_key:
            domain.extend(dashboard._get_section_domain(section_key))
            action_name = dashboard._get_statement_layout()["labels"].get(
                section_key, _("Journal Items")
            )
        else:
            raise UserError(_("Please choose an account or section to inspect."))
        return {
            "type": "ir.actions.act_window",
            "name": action_name,
            "res_model": "account.move.line",
            "view_mode": "list,pivot,graph",
            "views": [(False, "list"), (False, "pivot"), (False, "graph")],
            "domain": domain,
            "target": "current",
            "context": {
                "search_default_group_by_account": 1,
                "search_default_group_by_journal": 0,
            },
        }

    def _with_dashboard_context(self, options):
        return self.with_context(allowed_company_ids=options["company_ids"])

    def _build_dashboard_payload(self, options):
        selected_companies = self.env["res.company"].browse(options["company_ids"])
        display_company = self.env.company if self.env.company in selected_companies else (selected_companies[:1] or self.env.company)
        target_currency = display_company.currency_id
        company_by_id = {company.id: company for company in selected_companies}
        accessible_company_ids = self._get_accessible_company_ids()
        is_consolidated = len(selected_companies) > 1
        is_all_companies = set(options["company_ids"]) == set(accessible_company_ids)
        current_metrics = self._compute_metrics(options, company_by_id, target_currency)
        previous_metrics = None
        if options["compare_enabled"]:
            previous_metrics = self._compute_metrics(
                self._make_previous_period_options(options),
                company_by_id,
                target_currency,
            )
        statement = self._build_statement(options, company_by_id, target_currency)
        charts = self._build_charts(options, company_by_id, target_currency)
        company_currency_count = len(selected_companies.mapped("currency_id"))
        period_label = self._format_period_label(options["date_from"], options["date_to"])
        previous_options = self._make_previous_period_options(options)
        return {
            "meta": {
                "currency_id": target_currency.id,
                "currency_name": target_currency.name,
                "display_company_name": display_company.display_name,
                "company_names": selected_companies.mapped("display_name"),
                "selected_company_count": len(selected_companies),
                "is_consolidated": is_consolidated,
                "is_all_companies": is_all_companies,
                "scope_label": (
                    _("Consolidated - All Companies")
                    if is_all_companies
                    else _("Consolidated - %(count)s Companies", count=len(selected_companies))
                    if is_consolidated
                    else display_company.display_name
                ),
                "period_label": period_label,
                "comparison_label": self._format_period_label(
                    previous_options["date_from"], previous_options["date_to"]
                ),
                "today": fields.Date.to_string(fields.Date.context_today(self)),
                "last_refresh": fields.Datetime.now().isoformat(),
                "granularity": charts["trend"]["granularity"],
                "multi_currency_note": company_currency_count > 1
                and _(
                    "Amounts are converted to %(currency)s using each company's rate on the relevant bucket date.",
                    currency=target_currency.name,
                )
                or False,
            },
            "filters": self._serialize_options(options),
            "kpis": self._build_kpis(current_metrics, previous_metrics),
            "statement": statement,
            "charts": charts,
        }

    def _compute_metrics(self, options, company_by_id, target_currency):
        groups = self.env["account.move.line"].read_group(
            self._get_base_domain(options),
            ["balance:sum"],
            ["company_id", "account_type"],
            lazy=False,
        )
        metrics = {
            "revenue": 0.0,
            "other_income": 0.0,
            "cogs": 0.0,
            "operating_expenses": 0.0,
        }
        for group in groups:
            company = company_by_id.get(group["company_id"][0]) if group.get("company_id") else False
            account_type = group.get("account_type")
            converted_balance = self._convert_balance_amount(
                group.get("balance", 0.0),
                company,
                target_currency,
                options["date_to"],
            )
            amount = self._normalize_balance(account_type, converted_balance)
            if account_type == "income":
                metrics["revenue"] += amount
            elif account_type == "income_other":
                metrics["other_income"] += amount
            elif account_type == "expense_direct_cost":
                metrics["cogs"] += amount
            elif account_type in ("expense", "expense_depreciation"):
                metrics["operating_expenses"] += amount
        metrics["gross_profit"] = metrics["revenue"] - metrics["cogs"]
        metrics["total_expenses"] = metrics["cogs"] + metrics["operating_expenses"]
        metrics["operating_profit"] = metrics["gross_profit"] - metrics["operating_expenses"]
        metrics["net_profit"] = metrics["operating_profit"] + metrics["other_income"]
        metrics["gross_margin_pct"] = (
            (metrics["gross_profit"] / metrics["revenue"]) * 100.0
            if metrics["revenue"]
            else 0.0
        )
        rounded_metrics = {}
        for key, value in metrics.items():
            if key == "gross_margin_pct":
                rounded_metrics[key] = round(value, 2)
            else:
                rounded_metrics[key] = target_currency.round(value)
        return rounded_metrics

    def _build_kpis(self, current_metrics, previous_metrics):
        return {
            "revenue": self._make_metric_payload(
                _("Total Revenue"),
                "monetary",
                current_metrics["revenue"],
                previous_metrics and previous_metrics["revenue"],
                improves_when="up",
            ),
            "expenses": self._make_metric_payload(
                _("Total Expenses"),
                "monetary",
                current_metrics["total_expenses"],
                previous_metrics and previous_metrics["total_expenses"],
                improves_when="down",
            ),
            "net_profit": self._make_metric_payload(
                _("Net Profit / Loss"),
                "monetary",
                current_metrics["net_profit"],
                previous_metrics and previous_metrics["net_profit"],
                improves_when="up",
            ),
            "gross_margin_pct": self._make_metric_payload(
                _("Gross Margin %"),
                "percentage",
                current_metrics["gross_margin_pct"],
                previous_metrics and previous_metrics["gross_margin_pct"],
                improves_when="up",
            ),
            "operating_profit": self._make_metric_payload(
                _("Operating Profit"),
                "monetary",
                current_metrics["operating_profit"],
                previous_metrics and previous_metrics["operating_profit"],
                improves_when="up",
            ),
        }

    def _build_statement(self, options, company_by_id, target_currency):
        groups = self.env["account.move.line"].read_group(
            self._get_base_domain(options),
            ["balance:sum"],
            ["company_id", "account_id"],
            lazy=False,
        )
        account_ids = [
            group["account_id"][0] for group in groups if group.get("account_id")
        ]
        accounts = {
            account.id: account
            for account in self.env["account.account"].browse(account_ids)
        }
        sections = {
            "income": {"key": "income", "label": _("Income"), "amount": 0.0, "accounts": {}},
            "cogs": {
                "key": "cogs",
                "label": _("Cost of Goods Sold"),
                "amount": 0.0,
                "accounts": {},
            },
            "expenses": {
                "key": "expenses",
                "label": _("Expenses"),
                "amount": 0.0,
                "accounts": {},
            },
            "other_income": {
                "key": "other_income",
                "label": _("Other Income"),
                "amount": 0.0,
                "accounts": {},
            },
        }
        for group in groups:
            if not group.get("account_id"):
                continue
            account = accounts.get(group["account_id"][0])
            if not account:
                continue
            section_key = self._SECTION_BY_ACCOUNT_TYPE.get(account.account_type)
            if not section_key:
                continue
            company = company_by_id.get(group["company_id"][0]) if group.get("company_id") else False
            converted_balance = self._convert_balance_amount(
                group.get("balance", 0.0),
                company,
                target_currency,
                options["date_to"],
            )
            amount = self._normalize_balance(account.account_type, converted_balance)
            if target_currency.is_zero(amount):
                continue
            section = sections[section_key]
            account_bucket = section["accounts"].setdefault(
                account.id,
                {
                    "id": account.id,
                    "code": account.code or "",
                    "name": account.name,
                    "amount": 0.0,
                    "account_type": account.account_type,
                    "group_name": account.group_id.display_name if account.group_id else "",
                },
            )
            account_bucket["amount"] += amount
            section["amount"] += amount
        for section in sections.values():
            account_rows = list(section["accounts"].values())
            account_rows.sort(key=lambda row: (row["code"], row["name"]))
            section["accounts"] = account_rows
            section["amount"] = target_currency.round(section["amount"])
            for account_row in section["accounts"]:
                account_row["amount"] = target_currency.round(account_row["amount"])
        totals = {
            "gross_profit": target_currency.round(sections["income"]["amount"] - sections["cogs"]["amount"]),
            "operating_profit": target_currency.round(
                sections["income"]["amount"] - sections["cogs"]["amount"] - sections["expenses"]["amount"]
            ),
        }
        totals["net_profit"] = target_currency.round(
            totals["operating_profit"] + sections["other_income"]["amount"]
        )
        summary_rows = [
            {"key": "gross_profit", "label": _("Gross Profit"), "amount": totals["gross_profit"]},
            {
                "key": "operating_profit",
                "label": _("Operating Profit"),
                "amount": totals["operating_profit"],
            },
            {"key": "net_profit", "label": _("Net Profit / Loss"), "amount": totals["net_profit"]},
        ]
        return {
            "sections": [sections[key] for key in ("income", "cogs", "expenses", "other_income")],
            "sections_map": {key: sections[key] for key in ("income", "cogs", "expenses", "other_income")},
            "summary_rows": summary_rows,
            "summary_map": {row["key"]: row for row in summary_rows},
            "layout": self._get_statement_layout()["rows"],
        }

    def _build_charts(self, options, company_by_id, target_currency):
        granularity = self._get_granularity(options)
        trend_groups = self.env["account.move.line"].read_group(
            self._get_base_domain(options),
            ["balance:sum"],
            [f"date:{granularity}", "company_id", "account_type"],
            lazy=False,
        )
        buckets = defaultdict(
            lambda: {
                "revenue": 0.0,
                "other_income": 0.0,
                "cogs": 0.0,
                "operating_expenses": 0.0,
                "start": False,
                "end": False,
            }
        )
        for group in trend_groups:
            bucket_start, bucket_end = self._extract_bucket_dates(group)
            if not bucket_start:
                continue
            company = company_by_id.get(group["company_id"][0]) if group.get("company_id") else False
            account_type = group.get("account_type")
            converted_balance = self._convert_balance_amount(
                group.get("balance", 0.0),
                company,
                target_currency,
                bucket_end or options["date_to"],
            )
            amount = self._normalize_balance(account_type, converted_balance)
            bucket = buckets[bucket_start]
            bucket["start"] = bucket_start
            bucket["end"] = bucket_end or bucket_start
            if account_type == "income":
                bucket["revenue"] += amount
            elif account_type == "income_other":
                bucket["other_income"] += amount
            elif account_type == "expense_direct_cost":
                bucket["cogs"] += amount
            elif account_type in ("expense", "expense_depreciation"):
                bucket["operating_expenses"] += amount
        ordered_buckets = [buckets[key] for key in sorted(buckets)]
        trend_labels = [
            self._format_bucket_label(bucket["start"], bucket["end"], granularity)
            for bucket in ordered_buckets
        ]
        revenue_values = [target_currency.round(bucket["revenue"]) for bucket in ordered_buckets]
        expense_values = [
            target_currency.round(bucket["cogs"] + bucket["operating_expenses"])
            for bucket in ordered_buckets
        ]
        profit_values = [
            target_currency.round(
                bucket["revenue"] - bucket["cogs"] - bucket["operating_expenses"] + bucket["other_income"]
            )
            for bucket in ordered_buckets
        ]
        expense_breakdown = self._build_expense_breakdown(
            options, company_by_id, target_currency
        )
        return {
            "trend": {
                "granularity": granularity,
                "labels": trend_labels,
                "revenue": revenue_values,
                "expenses": expense_values,
                "profit": profit_values,
            },
            "expenses_breakdown": expense_breakdown,
        }

    def _build_expense_breakdown(self, options, company_by_id, target_currency):
        breakdown_domain = self._get_base_domain(options) + [
            ("account_type", "in", ("expense_direct_cost", "expense", "expense_depreciation"))
        ]
        groups = self.env["account.move.line"].read_group(
            breakdown_domain,
            ["balance:sum"],
            ["company_id", "account_id"],
            lazy=False,
        )
        account_ids = [
            group["account_id"][0] for group in groups if group.get("account_id")
        ]
        accounts = {
            account.id: account
            for account in self.env["account.account"].browse(account_ids)
        }
        categories = defaultdict(float)
        for group in groups:
            if not group.get("account_id"):
                continue
            account = accounts.get(group["account_id"][0])
            if not account:
                continue
            company = company_by_id.get(group["company_id"][0]) if group.get("company_id") else False
            converted_balance = self._convert_balance_amount(
                group.get("balance", 0.0),
                company,
                target_currency,
                options["date_to"],
            )
            amount = self._normalize_balance(account.account_type, converted_balance)
            label = account.group_id.display_name if account.group_id else account.name
            categories[label] += amount
        sorted_items = sorted(categories.items(), key=lambda item: item[1], reverse=True)
        primary_items = sorted_items[:6]
        others_total = sum(amount for _, amount in sorted_items[6:])
        labels = [label for label, _amount in primary_items]
        values = [target_currency.round(amount) for _label, amount in primary_items]
        if others_total:
            labels.append(_("Other"))
            values.append(target_currency.round(others_total))
        return {"labels": labels, "values": values}

    def _get_base_domain(self, options):
        domain = [
            ("parent_state", "=", "posted"),
            ("date", ">=", options["date_from"]),
            ("date", "<=", options["date_to"]),
            ("company_id", "in", options["company_ids"]),
            ("account_type", "in", self._ACCOUNT_TYPES),
        ]
        if options["journal_ids"]:
            domain.append(("journal_id", "in", options["journal_ids"]))
        if options["analytic_account_ids"]:
            domain.append(("analytic_distribution", "in", options["analytic_account_ids"]))
        return domain

    def _get_section_domain(self, section_key):
        if section_key == "income":
            return [("account_type", "=", "income")]
        if section_key == "other_income":
            return [("account_type", "=", "income_other")]
        if section_key == "cogs":
            return [("account_type", "=", "expense_direct_cost")]
        if section_key == "expenses":
            return [("account_type", "in", ("expense", "expense_depreciation"))]
        raise UserError(_("Unknown Profit & Loss section: %s", section_key))

    def _normalize_options(self, options):
        accessible_company_ids = self._get_accessible_company_ids()
        requested_company_ids = self._ordered_unique_ids(
            options.get("company_ids") or accessible_company_ids
        )
        selected_company_ids = [
            company_id for company_id in requested_company_ids if company_id in accessible_company_ids
        ] or accessible_company_ids
        period_type = options.get("period_type") or "month"
        date_from = self._to_date(options.get("date_from"))
        date_to = self._to_date(options.get("date_to"))
        if not date_from or not date_to:
            date_from, date_to = self._get_period_dates(period_type)
        if date_from > date_to:
            raise UserError(_("The start date must be earlier than the end date."))
        return {
            "period_type": period_type,
            "date_from": date_from,
            "date_to": date_to,
            "company_ids": selected_company_ids,
            "journal_ids": self._ordered_unique_ids(options.get("journal_ids") or []),
            "analytic_account_ids": self._ordered_unique_ids(options.get("analytic_account_ids") or []),
            "compare_enabled": bool(options.get("compare_enabled", True)),
        }

    def _serialize_options(self, options):
        return {
            "period_type": options["period_type"],
            "date_from": fields.Date.to_string(options["date_from"]),
            "date_to": fields.Date.to_string(options["date_to"]),
            "company_ids": options["company_ids"],
            "journal_ids": options["journal_ids"],
            "analytic_account_ids": options["analytic_account_ids"],
            "compare_enabled": options["compare_enabled"],
        }

    def _make_previous_period_options(self, options):
        previous_end = options["date_from"] - timedelta(days=1)
        delta_days = (options["date_to"] - options["date_from"]).days
        previous_start = previous_end - timedelta(days=delta_days)
        previous_options = dict(options)
        previous_options["date_from"] = previous_start
        previous_options["date_to"] = previous_end
        return previous_options

    def _get_period_dates(self, period_type):
        today = fields.Date.context_today(self)
        if period_type == "quarter":
            first_month = ((today.month - 1) // 3) * 3 + 1
            date_from = today.replace(month=first_month, day=1)
            date_to = date_from + relativedelta(months=3, days=-1)
        elif period_type == "year":
            date_from = today.replace(month=1, day=1)
            date_to = today.replace(month=12, day=31)
        else:
            date_from = today.replace(day=1)
            date_to = date_from + relativedelta(months=1, days=-1)
        return date_from, date_to

    def _get_granularity(self, options):
        if options["period_type"] == "month":
            return "day"
        if options["period_type"] == "quarter":
            return "week"
        if options["period_type"] == "year":
            return "month"
        day_span = (options["date_to"] - options["date_from"]).days
        if day_span <= 31:
            return "day"
        if day_span <= 120:
            return "week"
        return "month"

    def _extract_bucket_dates(self, group):
        range_value = group.get("__range") or {}
        if isinstance(range_value, dict):
            if "date" in range_value and isinstance(range_value["date"], dict):
                date_from = self._to_date(range_value["date"].get("from"))
                date_to = self._to_date(range_value["date"].get("to"))
                return date_from, date_to and (date_to - timedelta(days=1))
            for value in range_value.values():
                if isinstance(value, dict):
                    date_from = self._to_date(value.get("from"))
                    date_to = self._to_date(value.get("to"))
                    return date_from, date_to and (date_to - timedelta(days=1))
        for key, value in group.items():
            if key.startswith("date:") and value:
                bucket_date = self._to_date(value)
                return bucket_date, bucket_date
        return False, False

    def _format_bucket_label(self, date_from, date_to, granularity):
        if granularity == "month":
            return date_from.strftime("%b %Y")
        if granularity == "week":
            return "%s - %s" % (date_from.strftime("%d %b"), date_to.strftime("%d %b"))
        return date_from.strftime("%d %b")

    def _format_period_label(self, date_from, date_to):
        return "%s - %s" % (format_date(self.env, date_from), format_date(self.env, date_to))

    def _convert_balance_amount(self, amount, source_company, target_currency, date_to):
        if not source_company or source_company.currency_id == target_currency:
            return amount
        return source_company.currency_id._convert(
            amount,
            target_currency,
            source_company,
            date_to,
        )

    def _normalize_balance(self, account_type, amount):
        return -amount if account_type in ("income", "income_other") else amount

    def _make_metric_payload(self, label, value_type, current_value, previous_value, improves_when):
        comparison = {
            "enabled": previous_value is not None,
            "delta": previous_value is not None and (current_value - previous_value) or 0.0,
            "delta_pct": False,
            "previous_value": previous_value,
            "improves_when": improves_when,
        }
        if previous_value not in (None, 0):
            comparison["delta_pct"] = ((current_value - previous_value) / previous_value) * 100.0
        return {
            "label": label,
            "type": value_type,
            "value": current_value,
            "comparison": comparison,
        }

    def _get_statement_layout(self):
        return {
            "labels": {
                "income": _("Income"),
                "cogs": _("Cost of Goods Sold"),
                "expenses": _("Expenses"),
                "other_income": _("Other Income"),
            },
            "rows": [
                {"type": "section", "key": "income"},
                {"type": "section", "key": "cogs"},
                {"type": "summary", "key": "gross_profit"},
                {"type": "section", "key": "expenses"},
                {"type": "summary", "key": "operating_profit"},
                {"type": "section", "key": "other_income"},
                {"type": "summary", "key": "net_profit"},
            ],
        }

    def _ordered_unique_ids(self, values):
        seen = set()
        ordered_ids = []
        for value in values:
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
            if value not in seen:
                seen.add(value)
                ordered_ids.append(value)
        return ordered_ids

    def _get_accessible_company_ids(self):
        return self._ordered_unique_ids(self.env.user.company_ids.ids) or [self.env.company.id]

    def _to_date(self, value):
        if not value:
            return False
        if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
            return value
        return fields.Date.to_date(value)

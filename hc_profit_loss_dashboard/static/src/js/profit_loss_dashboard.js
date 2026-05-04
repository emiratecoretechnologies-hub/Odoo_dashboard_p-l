/** @odoo-module **/

import { loadBundle } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";
import { MultiRecordSelector } from "@web/core/record_selectors/multi_record_selector";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { formatFloat, formatMonetary } from "@web/views/fields/formatters";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

import { Component, onWillStart, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";

const ACTION_TAG = "hc_profit_loss_dashboard.action";

export class ProfitLossDashboard extends Component {
    static template = "hc_profit_loss_dashboard.Dashboard";
    static props = { ...standardActionServiceProps };
    static components = { MultiRecordSelector };
    static path = "profit-loss-dashboard";

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.notification = useService("notification");
        this.companyService = useService("company");

        this.trendChartRef = useRef("trendChart");
        this.barChartRef = useRef("barChart");
        this.pieChartRef = useRef("pieChart");
        this.chartInstances = {};
        this.animationFrame = null;

        const allCompanyIds = this.allCompanyIds;

        this.state = useState({
            loading: true,
            exporting: false,
            today: null,
            currencyId: null,
            dashboard: null,
            chartNonce: 0,
            metricNonce: 0,
            filters: {
                period_type: "month",
                date_from: "",
                date_to: "",
                company_ids: [...allCompanyIds],
                journal_ids: [],
                analytic_account_ids: [],
                compare_enabled: true,
            },
            appliedFilters: {
                period_type: "month",
                date_from: "",
                date_to: "",
                company_ids: [...allCompanyIds],
                journal_ids: [],
                analytic_account_ids: [],
                compare_enabled: true,
            },
            expandedSections: {
                income: true,
                cogs: true,
                expenses: true,
                other_income: true,
            },
            animatedKpis: {},
        });

        onWillStart(async () => {
            await loadBundle("web.chartjs_lib");
            await this.loadDashboard();
        });

        useEffect(
            () => {
                if (this.state.dashboard) {
                    this.renderCharts();
                }
                return () => this.destroyCharts();
            },
            () => [this.state.chartNonce]
        );

        useEffect(
            () => {
                if (this.state.dashboard) {
                    this.animateKpis();
                }
            },
            () => [this.state.metricNonce]
        );

        onWillUnmount(() => {
            this.destroyCharts();
            if (this.animationFrame) {
                cancelAnimationFrame(this.animationFrame);
            }
        });
    }

    async loadDashboard() {
        try {
            this.state.loading = true;
            const result = await this.orm.call(
                "hc.profit.loss.dashboard.wizard",
                "get_dashboard_data",
                [],
                { options: this.state.filters }
            );
            this.state.dashboard = result;
            this.state.currencyId = result.meta.currency_id;
            this.state.today = result.meta.today;
            Object.assign(this.state.appliedFilters, result.filters);
            Object.assign(this.state.filters, result.filters);
            this.state.chartNonce += 1;
            this.state.metricNonce += 1;
        } catch (error) {
            this.notification.add(
                error.message || _t("Unable to load the Profit & Loss dashboard."),
                { type: "danger", title: _t("Dashboard Error") }
            );
        } finally {
            this.state.loading = false;
        }
    }

    get allCompanyIds() {
        return Object.values(this.companyService.allowedCompanies)
            .map((company) => company.id)
            .sort((left, right) => left - right);
    }

    get companyDomain() {
        return [["id", "in", Object.values(this.companyService.allowedCompanies).map((company) => company.id)]];
    }

    get journalDomain() {
        return [["company_id", "in", this.state.filters.company_ids]];
    }

    get analyticDomain() {
        return [
            "|",
            ["company_id", "=", false],
            ["company_id", "in", this.state.filters.company_ids],
        ];
    }

    get companySelectorProps() {
        return {
            resIds: this.state.filters.company_ids,
            resModel: "res.company",
            update: (ids) => this.updateCompanies(ids),
            domain: this.companyDomain,
            placeholder: _t("Companies"),
            fieldString: _t("Companies"),
        };
    }

    get journalSelectorProps() {
        return {
            resIds: this.state.filters.journal_ids,
            resModel: "account.journal",
            update: (ids) => this.updateFilterIds("journal_ids", ids),
            domain: this.journalDomain,
            placeholder: _t("Journals"),
            fieldString: _t("Journals"),
        };
    }

    get analyticSelectorProps() {
        return {
            resIds: this.state.filters.analytic_account_ids,
            resModel: "account.analytic.account",
            update: (ids) => this.updateFilterIds("analytic_account_ids", ids),
            domain: this.analyticDomain,
            placeholder: _t("Analytic Accounts"),
            fieldString: _t("Analytic Accounts"),
        };
    }

    get sectionsByKey() {
        return Object.fromEntries((this.state.dashboard?.statement.sections || []).map((section) => [section.key, section]));
    }

    get summaryRowsByKey() {
        return Object.fromEntries((this.state.dashboard?.statement.summary_rows || []).map((row) => [row.key, row]));
    }

    updateCompanies(ids) {
        const companyIds = this.uniqueIds(ids);
        this.state.filters.company_ids = companyIds.length
            ? companyIds
            : [...this.allCompanyIds];
        this.state.filters.journal_ids = [];
        this.state.filters.analytic_account_ids = [];
    }

    updateFilterIds(key, ids) {
        this.state.filters[key] = this.uniqueIds(ids);
    }

    uniqueIds(ids) {
        return [...new Set((ids || []).map((id) => Number(id)).filter((id) => !Number.isNaN(id)))];
    }

    formatMoney(value) {
        return formatMonetary(value || 0, {
            currencyId: this.state.currencyId,
        });
    }

    formatPercent(value) {
        return `${formatFloat(value || 0, { digits: [0, 2] })}%`;
    }

    metricValue(metricKey) {
        const metric = this.state.animatedKpis[metricKey] || this.state.dashboard?.kpis?.[metricKey];
        if (!metric) {
            return "";
        }
        return metric.type === "percentage"
            ? this.formatPercent(metric.value)
            : this.formatMoney(metric.value);
    }

    metricDelta(metricKey) {
        const metric = this.state.dashboard?.kpis?.[metricKey];
        if (!metric?.comparison?.enabled) {
            return _t("No comparison");
        }
        const delta = metric.comparison.delta || 0;
        if (metric.type === "percentage") {
            return `${delta >= 0 ? "+" : ""}${formatFloat(delta, { digits: [0, 2] })} pts`;
        }
        return `${delta >= 0 ? "+" : ""}${this.formatMoney(delta)}`;
    }

    metricDeltaClass(metricKey) {
        const metric = this.state.dashboard?.kpis?.[metricKey];
        if (!metric?.comparison?.enabled) {
            return "text-muted";
        }
        const delta = metric.comparison.delta || 0;
        const improvesWhen = metric.comparison.improves_when;
        const isPositive = improvesWhen === "down" ? delta <= 0 : delta >= 0;
        return isPositive ? "text-success" : "text-danger";
    }

    cardClass(metricKey) {
        const metric = this.state.dashboard?.kpis?.[metricKey];
        if (!metric) {
            return "";
        }
        const value = metric.value || 0;
        if (metricKey === "net_profit" || metricKey === "operating_profit") {
            return value >= 0 ? "is-profit" : "is-loss";
        }
        if (metricKey === "gross_margin_pct") {
            return value >= 0 ? "is-profit" : "is-loss";
        }
        return "";
    }

    async applyFilters() {
        if (this.state.filters.date_from && this.state.filters.date_to && this.state.filters.date_from > this.state.filters.date_to) {
            this.notification.add(_t("The start date must be earlier than the end date."), {
                type: "warning",
                title: _t("Invalid Date Range"),
            });
            return;
        }
        await this.loadDashboard();
    }

    resetFilters() {
        const defaults = this.getPresetDates("month");
        this.state.filters.period_type = "month";
        this.state.filters.date_from = defaults.date_from;
        this.state.filters.date_to = defaults.date_to;
        this.state.filters.company_ids = [...this.allCompanyIds];
        this.state.filters.journal_ids = [];
        this.state.filters.analytic_account_ids = [];
        this.state.filters.compare_enabled = true;
    }

    applyPreset(periodType) {
        const dates = this.getPresetDates(periodType);
        this.state.filters.period_type = periodType;
        this.state.filters.date_from = dates.date_from;
        this.state.filters.date_to = dates.date_to;
    }

    getPresetDates(periodType) {
        const anchorDate = new Date(this.state.today || new Date().toISOString().slice(0, 10));
        const year = anchorDate.getUTCFullYear();
        const month = anchorDate.getUTCMonth();
        if (periodType === "year") {
            return {
                date_from: `${year}-01-01`,
                date_to: `${year}-12-31`,
            };
        }
        if (periodType === "quarter") {
            const quarterStart = Math.floor(month / 3) * 3;
            const from = new Date(Date.UTC(year, quarterStart, 1));
            const to = new Date(Date.UTC(year, quarterStart + 3, 0));
            return {
                date_from: from.toISOString().slice(0, 10),
                date_to: to.toISOString().slice(0, 10),
            };
        }
        const from = new Date(Date.UTC(year, month, 1));
        const to = new Date(Date.UTC(year, month + 1, 0));
        return {
            date_from: from.toISOString().slice(0, 10),
            date_to: to.toISOString().slice(0, 10),
        };
    }

    toggleSection(key) {
        this.state.expandedSections[key] = !this.state.expandedSections[key];
    }

    isExpanded(key) {
        return !!this.state.expandedSections[key];
    }

    async openSection(key) {
        const action = await this.orm.call(
            "hc.profit.loss.dashboard.wizard",
            "action_open_move_lines",
            [],
            { options: this.state.appliedFilters, section_key: key }
        );
        return this.actionService.doAction(action);
    }

    async openAccount(accountId) {
        const action = await this.orm.call(
            "hc.profit.loss.dashboard.wizard",
            "action_open_move_lines",
            [],
            { options: this.state.appliedFilters, account_ids: [accountId] }
        );
        return this.actionService.doAction(action);
    }

    async exportReport(methodName) {
        try {
            this.state.exporting = true;
            const action = await this.orm.call(
                "hc.profit.loss.dashboard.wizard",
                methodName,
                [],
                { options: this.state.appliedFilters }
            );
            await this.actionService.doAction(action);
        } catch (error) {
            this.notification.add(error.message || _t("Unable to export the dashboard."), {
                type: "danger",
                title: _t("Export Error"),
            });
        } finally {
            this.state.exporting = false;
        }
    }

    animateKpis() {
        if (this.animationFrame) {
            cancelAnimationFrame(this.animationFrame);
        }
        const start = performance.now();
        const duration = 650;
        const source = this.state.dashboard?.kpis || {};
        const initialValues = {};
        Object.entries(source).forEach(([key, metric]) => {
            initialValues[key] = this.state.animatedKpis[key]?.value ?? 0;
            this.state.animatedKpis[key] = { ...metric, value: initialValues[key] };
        });
        const step = (timestamp) => {
            const progress = Math.min((timestamp - start) / duration, 1);
            const eased = 1 - Math.pow(1 - progress, 3);
            Object.entries(source).forEach(([key, metric]) => {
                const startValue = initialValues[key] || 0;
                this.state.animatedKpis[key] = {
                    ...metric,
                    value: startValue + (metric.value - startValue) * eased,
                };
            });
            if (progress < 1) {
                this.animationFrame = requestAnimationFrame(step);
            }
        };
        this.animationFrame = requestAnimationFrame(step);
    }

    destroyCharts() {
        Object.values(this.chartInstances).forEach((chart) => chart?.destroy());
        this.chartInstances = {};
    }

    renderCharts() {
        this.destroyCharts();
        const trend = this.state.dashboard?.charts?.trend || { labels: [], revenue: [], expenses: [], profit: [] };
        const expensesBreakdown = this.state.dashboard?.charts?.expenses_breakdown || { labels: [], values: [] };

        if (this.trendChartRef.el) {
            this.chartInstances.trend = new Chart(this.trendChartRef.el, {
                type: "line",
                data: {
                    labels: trend.labels,
                    datasets: [
                        {
                            label: _t("Net Profit"),
                            data: trend.profit,
                            borderColor: "#176B87",
                            backgroundColor: "rgba(23, 107, 135, 0.12)",
                            fill: true,
                            tension: 0.32,
                            pointRadius: 3,
                            pointBackgroundColor: "#176B87",
                        },
                    ],
                },
                options: this.commonChartOptions(),
            });
        }

        if (this.barChartRef.el) {
            this.chartInstances.bar = new Chart(this.barChartRef.el, {
                type: "bar",
                data: {
                    labels: trend.labels,
                    datasets: [
                        {
                            label: _t("Revenue"),
                            data: trend.revenue,
                            backgroundColor: "#1F7A8C",
                            borderRadius: 8,
                        },
                        {
                            label: _t("Expenses"),
                            data: trend.expenses,
                            backgroundColor: "#BFDBF7",
                            borderRadius: 8,
                        },
                    ],
                },
                options: {
                    ...this.commonChartOptions(),
                    scales: {
                        x: { grid: { display: false } },
                        y: { beginAtZero: true, ticks: { callback: (value) => this.shortCurrency(value) } },
                    },
                },
            });
        }

        if (this.pieChartRef.el) {
            this.chartInstances.pie = new Chart(this.pieChartRef.el, {
                type: "pie",
                data: {
                    labels: expensesBreakdown.labels,
                    datasets: [
                        {
                            data: expensesBreakdown.values,
                            backgroundColor: [
                                "#176B87",
                                "#3A7D44",
                                "#F4B860",
                                "#D95D39",
                                "#457B9D",
                                "#8E9AAF",
                                "#CAD2C5",
                            ],
                            borderColor: "#FFFFFF",
                            borderWidth: 2,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            position: "bottom",
                            labels: {
                                usePointStyle: true,
                                boxWidth: 10,
                            },
                        },
                    },
                },
            });
        }
    }

    commonChartOptions() {
        return {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: "bottom",
                    labels: {
                        usePointStyle: true,
                        boxWidth: 10,
                    },
                },
                tooltip: {
                    callbacks: {
                        label: (context) => `${context.dataset.label}: ${this.formatMoney(context.parsed.y ?? context.parsed)}`,
                    },
                },
            },
        };
    }

    shortCurrency(value) {
        const absolute = Math.abs(value || 0);
        if (absolute >= 1000000) {
            return `${(value / 1000000).toFixed(1)}M`;
        }
        if (absolute >= 1000) {
            return `${(value / 1000).toFixed(1)}K`;
        }
        return `${Math.round(value || 0)}`;
    }
}

registry.category("actions").add(ACTION_TAG, ProfitLossDashboard);

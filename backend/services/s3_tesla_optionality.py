"""Scenario analysis for Tesla's evidenced early commercial services."""

from decimal import Decimal, localcontext

from backend.services.market_data.quality_valuation import dec, text

CODE = 'backend/services/s3_tesla_optionality.py'
METHOD = 'TSLA_FSD_ROBOTAXI_INCREMENTAL_CONDITIONAL_V1'
OPERATING_ACCESSION = '0001628280-26-066738'

# Forward scenario parameters, never issuer forecasts or observed economics.
# Each fleet path is time-average owned vehicles in permitted commercial service.
SCENARIOS = {
    'cautious': dict(active_fsd_m=('1.8', '2.0', '2.2', '2.4', '2.6'),
                     recurring_paid_fraction='.45', monthly_net_price='75',
                     permitted_metros=(7, 7, 8, 8, 8),
                     average_fleet=(1500, 3000, 6000, 11000, 18000), paid_miles='12000',
                     collected_fare_per_mile='1.00', cash_cost_per_paid_mile='.90',
                     vehicle_and_launch_cost='48000', tax_rate='.25',
                     extra_r_and_d_m=('65', '75', '85', '95', '105'),
                     funding_cost='.09', terminal_after_tax_multiple='12',
                     spacex_net_recovery='.50'),
    'base': dict(active_fsd_m=('2.1', '2.5', '3.0', '3.5', '4.0'),
                 recurring_paid_fraction='.70', monthly_net_price='90',
                 permitted_metros=(7, 9, 12, 15, 18),
                 average_fleet=(4000, 10000, 23000, 45000, 80000), paid_miles='26000',
                 collected_fare_per_mile='1.30', cash_cost_per_paid_mile='.70',
                 vehicle_and_launch_cost='36000', tax_rate='.23',
                 extra_r_and_d_m=('130', '160', '190', '220', '250'),
                 funding_cost='.07', terminal_after_tax_multiple='16',
                 spacex_net_recovery='.75'),
    'optimistic': dict(active_fsd_m=('2.4', '3.3', '4.25', '5.35', '6.5'),
                       recurring_paid_fraction='.85', monthly_net_price='99',
                       permitted_metros=(7, 11, 18, 25, 35),
                       average_fleet=(8000, 22000, 55000, 115000, 230000), paid_miles='38000',
                       collected_fare_per_mile='1.65', cash_cost_per_paid_mile='.59',
                       vehicle_and_launch_cost='30000', tax_rate='.22',
                       extra_r_and_d_m=('240', '310', '380', '440', '510'),
                       funding_cost='.06', terminal_after_tax_multiple='20',
                       spacex_net_recovery='.90'),
}
OBSERVED = {
    'quarter_end': '2026-09-30',
    'active_fsd_accounts_m': '1.74',
    'active_fsd_definition': 'Includes upfront payments and subscription accounts. Excludes free trials.',
    'all_time_deliveries_m': '10.2',
    'all_time_deliveries_definition': 'Cumulative new deliveries and unsupervised Robotaxis in commercial operation, not eligible active subscribers.',
    'installed_annual_cybercab_capacity_more_than': 125000,
    'cybercab_capacity_definition': 'Installed annual production capacity, not an observed commercially active fleet.',
    'austin_cybercab_service_started': '2026-09-03',
    'cumulative_unsupervised_robotaxi_miles_approx': 2000000,
    'robotaxi_miles_definition': 'Unsupervised mileage, not billed paid miles, utilization or fare.',
    'optimus': 'Early ramp in California; Texas construction and no verified external paid volumes or unit economics.',
}
SCHEDULE = {
    'modeled_us_fsd_price_anchor_usd_monthly': '99',
    'core_robotaxi_paid_miles_annual_floor_assumption': '10000000',
    'fsd_variable_cash_cost_fraction': '.32',
    'robotaxi_asset_life_years': 6,
}


def _case(core, inputs, assets):
    """Roll incremental cash and externally funded shortfall before terminal equity."""
    fleet_prev = cumulative = Decimal(0)
    tax, funding = dec(inputs['tax_rate']), dec(inputs['funding_cost'])
    paid_miles, fare = dec(inputs['paid_miles']), dec(inputs['collected_fare_per_mile'])
    trip_cost = dec(inputs['cash_cost_per_paid_mile'])
    asset_cost = dec(inputs['vehicle_and_launch_cost'])
    deductions = dec(SCHEDULE['fsd_variable_cash_cost_fraction'])
    life = SCHEDULE['robotaxi_asset_life_years']
    rows = []
    for index, operating_year in enumerate(core['years']):
        core_scale = dec(operating_year['revenue_index'])
        # Treat all observed active accounts as if already recurring at the
        # modeled full U.S. list price. This is a core overlap allowance, not ARR.
        fsd_floor = (dec(OBSERVED['active_fsd_accounts_m']) * 1_000_000
                     * dec(SCHEDULE['modeled_us_fsd_price_anchor_usd_monthly']) * 12 * core_scale)
        fsd_gross = (dec(inputs['active_fsd_m'][index]) * 1_000_000
                     * dec(inputs['recurring_paid_fraction']) * dec(inputs['monthly_net_price']) * 12)
        fsd_net = max(Decimal(0), fsd_gross - fsd_floor)
        # Future assumptions are conditional on permits. The operating core
        # keeps an explicit paid-mile allowance, rather than adding all rides.
        miles_floor = dec(SCHEDULE['core_robotaxi_paid_miles_annual_floor_assumption']) * core_scale
        fleet_total = dec(inputs['average_fleet'][index])
        extra_miles = max(Decimal(0), fleet_total * paid_miles - miles_floor)
        extra_fleet = extra_miles / paid_miles
        recurring_depreciation = extra_fleet * asset_cost / life
        expansion_capex = max(Decimal(0), extra_fleet - fleet_prev) * asset_cost
        fleet_prev = extra_fleet
        r_and_d = dec(inputs['extra_r_and_d_m'][index]) * 1_000_000
        trip_revenue = extra_miles * fare
        ebit = fsd_net * (1-deductions) + extra_miles * (fare-trip_cost) - recurring_depreciation - r_and_d
        nopat = ebit - max(Decimal(0), ebit) * tax
        # Cash replacement outlay equals the recurring annualized depreciation proxy.
        funding_cost = -min(Decimal(0), cumulative) * funding
        cumulative += nopat - expansion_capex - funding_cost
        rows.append(dict(year=index+1, permitted_metros_assumption=inputs['permitted_metros'][index],
                         core_fsd_revenue_allowance_usd=text(fsd_floor), modeled_fsd_revenue_usd=text(fsd_gross),
                         incremental_fsd_revenue_usd=text(fsd_net), core_paid_miles_allowance=text(miles_floor),
                         incremental_paid_miles=text(extra_miles), incremental_trip_revenue_usd=text(trip_revenue),
                         annualized_depreciation_and_replacement_capex_usd=text(recurring_depreciation),
                         expansion_capex_usd=text(expansion_capex), incremental_r_and_d_usd=text(r_and_d),
                         after_tax_incremental_operating_usd=text(nopat),
                         funding_cost_usd=text(funding_cost), accumulated_net_cash_usd=text(cumulative)))
    terminal_increment = max(Decimal(0), nopat) * dec(inputs['terminal_after_tax_multiple']) + cumulative
    diluted = dec(assets['outstanding_common_shares']) * dec(core['years'][-1]['relative_shares'])
    spacex = dec(assets['spacex_carrying_usd']) * dec(inputs['spacex_net_recovery'])
    terminal = dec(core['terminal_wealth']) + (terminal_increment + spacex) / diluted
    return dict(operating_core_terminal_usd=core['terminal_wealth'],
                incremental_terminal_equity_usd=text(terminal_increment), spacex_net_terminal_usd=text(spacex),
                terminal_diluted_shares=text(diluted), terminal_per_share_usd=text(terminal),
                path=rows, scenario_inputs={key: list(value) if isinstance(value, tuple) else value
                                            for key, value in inputs.items()})


def reprice(card, price, remaining):
    """Recalculate returns against the eligible quote and original terminal wealth."""
    analysis = card.get('positive_optional_research')
    if analysis is None:
        return
    with localcontext() as ctx:
        ctx.prec = 34
        price, remaining = dec(price), dec(remaining)
        if price <= 0 or not Decimal(0) < remaining <= 5:
            raise ValueError('S3_TSLA_OPTIONALITY_REPRICE_CLOCK_OR_PRICE_INVALID')
        analysis.update(basis_price_usd=text(price), remaining_years=str(remaining))
        rate = dec(card.get('assumptions', {}).get('required_return', '.10'))
        for row in analysis['scenarios'].values():
            terminal = max(Decimal(0), dec(row['terminal_per_share_usd']))
            core = dec(row['operating_core_terminal_usd'])
            hurdle = max(Decimal(0), price * (1+rate)**remaining - terminal)
            row.update(annualized_return=text((terminal/price)**(1/remaining)-1),
                       total_return=text(terminal/price-1),
                       conditional_value=text(terminal/(1+rate)**remaining),
                       operating_core_conditional_value=text(core/(1+rate)**remaining),
                       operating_core_annualized_return=text((core/price)**(1/remaining)-1),
                       remaining_unmodeled_terminal_hurdle_per_share=text(hurdle),
                       remaining_unmodeled_terminal_hurdle_usd=text(hurdle*dec(row['terminal_diluted_shares'])))
        base = analysis['scenarios']['base']
        uplift = dec(base['terminal_per_share_usd']) - dec(base['operating_core_terminal_usd'])
        analysis['matched_core_stress'] = []
        for cell in card.get('joint_sensitivity', []):
            if not cell.get('result'):
                continue
            core = dec(cell['result']['terminal_wealth'])
            extended = max(Decimal(0), core+uplift)
            analysis['matched_core_stress'].append(dict(
                revenue_growth_shift=cell['revenue_growth_shift'], multiple_shift=cell['terminal_pe_shift'],
                core_conditional_value=text(core/(1+rate)**remaining),
                extended_conditional_value=text(extended/(1+rate)**remaining),
                core_annualized_return=text((core/price)**(1/remaining)-1),
                extended_annualized_return=text((extended/price)**(1/remaining)-1)))
        analysis['matched_core_stress_scope'] = 'SAME_CORE_STRESSES_FIXED_BASE_OPTION_VALUE; NO_OPTIMUS_MARK'
        analysis['extended_conditional_value_range'] = sorted(
            (row['conditional_value'] for row in analysis['scenarios'].values()), key=dec)[::2]
        analysis['operating_core_conditional_value_range'] = sorted(
            (row['operating_core_conditional_value'] for row in analysis['scenarios'].values()), key=dec)[::2]


def build(card, proof, start, end):
    if (proof.get('q3_operating_update', {}).get('accession') != OPERATING_ACCESSION
            or 'nonoperating_asset_stress' not in card):
        return
    assets = card['nonoperating_asset_stress']['reported']
    with localcontext() as ctx:
        ctx.prec = 34
        scenarios = {name:_case(card['scenarios'][name], inputs, assets)
                     for name, inputs in SCENARIOS.items()}
    card['positive_optional_research'] = dict(
        method=METHOD, status='BOUNDED_COMMERCIAL_OPTION_SCENARIOS', observed=OBSERVED,
        proof=proof, schedule_assumptions=SCHEDULE, scenarios=scenarios,
        forecast_origin=start.isoformat(), forecast_endpoint=end.isoformat(),
        optimus_evidence_status=dict(production='FREMONT_EARLY_RAMP_TEXAS_CONSTRUCTION',
            externally_qualified_model='UNAVAILABLE_PENDING_COMMERCIAL_UNIT_ECONOMICS',
            reopen_on=['NAMED_EXTERNAL_PAID_DEPLOYMENT_WITH_UNITS_OR_CONTRACTED_VOLUME',
                       'REPEATABLE_PRICE_AND_UTILIZATION_WITH_MANUFACTURING_AND_SERVICE_COST',
                       'CAPACITY_RAMP_CAPEX_AND_FUNDING_OR_DILUTION']),
        limitations=[
            'Scenario markets and permit expansion are conditions; the source establishes Austin deployment, not next-year permits.',
            'Fleet, monthly-paying mix, regional pricing, utilization, electricity, cleaning, support, insurance, build cost and incremental R&D are modeled, not current issuer measurements.',
            'Incremental passenger FSD and company-owned Robotaxi are distinct. Core revenue allowances reduce overlap; undisclosed business attribution prevents proving full exclusion.',
            'Operating share drift is retained; negative growth cash is financed with modeled interest-bearing funding, not assumed equity issuance. Debt access or additional financing dilution may alter realized returns.',
            'Separate SpaceX recovery is net of hypothetical tax and disposition frictions.',
            'Optimus external commercial units, price, cost and capacity remain unqualified. No estimated Optimus contribution or full-company qualification is implied.'
        ])
    reprice(card, card['cross_checks']['observed_price']['values']['observed_price'], Decimal(5))

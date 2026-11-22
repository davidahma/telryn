# Complaint deadline data

This separately versioned dataset describes selected complaint and error handling obligations. Each rule has its primary source, effective dates, triggering evidence and boundaries. Calendar files give dated public closures and coverage. Consumers can use the JSON files from any language. The files require no Telryn installation or network access.

Check the package's file hashes against `SHA256SUMS.txt`. The Telryn release also signs the outer archive's digest. Use `schema.json` for `rules.json` and `calendar.schema.json` for the individual calendar records. `effective_from` is the documented start for the recorded rule or current regulatory text edition. A null `effective_through` means no end was established at publication. Coverage for calculations depends on the selected calendar, independently of the effective dates of the rule.

Determine applicability, start and stop evidence, time zone and calendar before calculating a deadline. A `fixed` timer can be calculated after its conditions are satisfied. An `evidence_required` timer needs additional context such as an actual payment provider business schedule, a regulator-defined extension, or statement closing dates. These records describe selected paths. Check the full primary source for other routes and jurisdictional exceptions.

- UK local bank holidays are defined for England and Wales, Scotland and Northern Ireland. FCA DISP 1.6.2A and 2.8 use the relevant provider's transaction opening schedule. Use a firm calendar for those paths. [FCA business day definition](https://handbook.fca.org.uk/glossary/G120)
- Ireland's file lists public holidays through the end of 2026. Substitute employer closure dates must be established locally. [Workplace Relations Commission](https://www.workplacerelations.ie/en/what_you_should_know/public-holidays/)
- Australian records cover selected RG 271 calendar-day responses. Check sector and hardship exceptions against the guide. [ASIC RG 271](https://asic.gov.au/regulatory-resources/find-a-document/regulatory-guides/rg-271-internal-dispute-resolution/)
- Regulation E business days depend on the institution's functions. The Federal Reserve schedule describes the Reserve Banks. Regulation Z billing-error deadlines require actual billing-cycle boundaries. [Reg E definitions](https://www.consumerfinance.gov/rules-policy/regulations/1005/2/) · [Reg Z billing errors](https://www.consumerfinance.gov/rules-policy/regulations/1026/13/)

Maintainers can build the archive from the checkout with `python -m deadlines.build --out /path/telryn-deadlines-1.0.0.tar.gz`. Advance the data version when a source or coverage interval changes. Retain earlier archive versions for replay. Record the date and official source of new coverage before adding it.

SELECT e.case_id
FROM eligible AS e
WHERE (lower(e.message) LIKE '%complaint%' OR lower(e.message) LIKE '%investigat%')
  AND NOT EXISTS (SELECT 1 FROM register_at_cutoff AS r WHERE r.match_key = e.match_key)
  AND NOT EXISTS (
      SELECT 1 FROM owners_at_cutoff AS a
      WHERE a.case_id = e.case_id AND a.status IN ('accepted', 'completed')
  )
ORDER BY e.case_id
LIMIT :slots

SELECT id
FROM requests
ORDER BY json_extract(inputs, '$.risk') DESC, id ASC
LIMIT :capacity

SELECT id
FROM requests
ORDER BY json_extract(inputs, '$.revised') DESC, id ASC
LIMIT :capacity

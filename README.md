# ReadMe Doc Healer


## Notes
### Conversion of multiple Web API sources into on "best"
`build_best_openapi.py` generates the merged outputs (JSON and YAML). It lifts Postman request-body field descriptions into the OpenAPI schema, keeps the stronger structural conversion, restores proper header apiKey auth, adds operationIds, adds both UAT and LIVE servers, and keeps duplicate-source metadata where the Postman collection collapses multiple workflows onto one method and path. Validation ran successfully.

For old/new version to remain useful as a ReadMe Doc Healer example: Postman collection is kept as-is, and the best-of OpenAPI represents the repaired result. Add to final before-and-after story.
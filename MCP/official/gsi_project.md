# Protocol Documentation
<a name="top"></a>

## Table of Contents

- [gsi_project.proto](#gsi_project-proto)
    - [AddRequest](#gsi-pb-project-AddRequest)
    - [AddResponse](#gsi-pb-project-AddResponse)
    - [DeleteRequest](#gsi-pb-project-DeleteRequest)
    - [DeleteResponse](#gsi-pb-project-DeleteResponse)
    - [GetRequest](#gsi-pb-project-GetRequest)
    - [GetResponse](#gsi-pb-project-GetResponse)
    - [LoadResultsRequest](#gsi-pb-project-LoadResultsRequest)
    - [LoadResultsResponse](#gsi-pb-project-LoadResultsResponse)
    - [ParamInfo](#gsi-pb-project-ParamInfo)
    - [ParamResults](#gsi-pb-project-ParamResults)
    - [QueryResultsAtCoordinatesRequest](#gsi-pb-project-QueryResultsAtCoordinatesRequest)
    - [QueryResultsAtCoordinatesResponse](#gsi-pb-project-QueryResultsAtCoordinatesResponse)
    - [QueryResultsAtCoordinatesResponse.ResultsEntry](#gsi-pb-project-QueryResultsAtCoordinatesResponse-ResultsEntry)
    - [QueryResultsAvailabilityRequest](#gsi-pb-project-QueryResultsAvailabilityRequest)
    - [QueryResultsAvailabilityResponse](#gsi-pb-project-QueryResultsAvailabilityResponse)
    - [QueryResultsRequest](#gsi-pb-project-QueryResultsRequest)
    - [QueryResultsResponse](#gsi-pb-project-QueryResultsResponse)
    - [QueryResultsResponse.ResultsEntry](#gsi-pb-project-QueryResultsResponse-ResultsEntry)
    - [QueryTableParamsInfoRequest](#gsi-pb-project-QueryTableParamsInfoRequest)
    - [QueryTableParamsInfoResponse](#gsi-pb-project-QueryTableParamsInfoResponse)
    - [SetRequest](#gsi-pb-project-SetRequest)
    - [SetResponse](#gsi-pb-project-SetResponse)
    - [SolveAnalysesRequest](#gsi-pb-project-SolveAnalysesRequest)
    - [SolveAnalysesResponse](#gsi-pb-project-SolveAnalysesResponse)
    - [SolveAnalysesResponse.CompletionStatusEntry](#gsi-pb-project-SolveAnalysesResponse-CompletionStatusEntry)
    - [SolveAnalysesResult](#gsi-pb-project-SolveAnalysesResult)
  
    - [Project](#gsi-pb-project-Project)
  
- [Scalar Value Types](#scalar-value-types)



<a name="gsi_project-proto"></a>
<p align="right"><a href="#top">Top</a></p>

## gsi_project.proto



<a name="gsi-pb-project-AddRequest"></a>

### AddRequest
Request message for adding a new object to a specified analysis.


| Field | Type | Label | Description |
| ----- | ---- | ----- | ----------- |
| analysis | [string](#string) | optional | Name of the analysis |
| object | [string](#string) | optional | Name of the new object to create |
| data | [google.protobuf.Value](#google-protobuf-Value) | optional | The data for the new object |






<a name="gsi-pb-project-AddResponse"></a>

### AddResponse
Response message for AddRequest.


| Field | Type | Label | Description |
| ----- | ---- | ----- | ----------- |
| object | [string](#string) | optional | Name of the newly added object |






<a name="gsi-pb-project-DeleteRequest"></a>

### DeleteRequest
Request message for deleting an object from an analysis.


| Field | Type | Label | Description |
| ----- | ---- | ----- | ----------- |
| analysis | [string](#string) | optional | Name of the analysis |
| object | [string](#string) | optional | Name of the object to delete |
| force_delete | [bool](#bool) | optional | If true, the object will be deleted even if it is referenced by other objects. |






<a name="gsi-pb-project-DeleteResponse"></a>

### DeleteResponse
Response message for DeleteRequest.






<a name="gsi-pb-project-GetRequest"></a>

### GetRequest
Request message for retrieving data for a specific analysis and object.


| Field | Type | Label | Description |
| ----- | ---- | ----- | ----------- |
| analysis | [string](#string) | optional | Name of the analysis |
| object | [string](#string) | optional | Name of the object |






<a name="gsi-pb-project-GetResponse"></a>

### GetResponse
Response message containing the requested data.


| Field | Type | Label | Description |
| ----- | ---- | ----- | ----------- |
| data | [google.protobuf.Value](#google-protobuf-Value) | optional | The retrieved data as a protobuf value. |






<a name="gsi-pb-project-LoadResultsRequest"></a>

### LoadResultsRequest
Request message for LoadResults
Takes an analysis name


| Field | Type | Label | Description |
| ----- | ---- | ----- | ----------- |
| analysis | [string](#string) | optional | Name of the queried analysis |






<a name="gsi-pb-project-LoadResultsResponse"></a>

### LoadResultsResponse
Response message for LoadResults
Empty response - successful completion is indicated by the absence of an error.






<a name="gsi-pb-project-ParamInfo"></a>

### ParamInfo
Metadata information for a single table parameter.
Describes the parameter type, display information, units, and vector components if applicable.


| Field | Type | Label | Description |
| ----- | ---- | ----- | ----------- |
| dataparam | [gsi.pb.DataParam.Type](#gsi-pb-DataParam-Type) | optional | The type of DataParam |
| key | [string](#string) | optional | The key of the DataParam |
| display | [string](#string) | optional | The display name for the DataParam |
| unit_category | [gsi.pb.UnitCategory.Type](#gsi-pb-UnitCategory-Type) | optional | The category of unit the DataParam belongs to. E.g. Length, Time, etc |
| vector_components | [gsi.pb.DataParam.Type](#gsi-pb-DataParam-Type) | repeated | Stores the X, Y, and optionally (depending on dimension) Z DataParamType components of vector DataParamTypes E.g. eWaterFlux has components eWaterFluxX, eWaterFluxY, and eWaterFluxZ |
| units | [string](#string) | optional | The units of the DataParam |






<a name="gsi-pb-project-ParamResults"></a>

### ParamResults
Inner message type. Contains a list of result values for a given DataParam


| Field | Type | Label | Description |
| ----- | ---- | ----- | ----------- |
| values | [double](#double) | repeated | List of result values |






<a name="gsi-pb-project-QueryResultsAtCoordinatesRequest"></a>

### QueryResultsAtCoordinatesRequest
Request message for Spatial Results Query
Takes an analysis name, a time step, a run number, an instance number, a list of requested DataParams,
and a list of the spatial coordinates to query at.


| Field | Type | Label | Description |
| ----- | ---- | ----- | ----------- |
| analysis | [string](#string) | optional | Name of the queried analysis. |
| step | [uint32](#uint32) | optional | Step number to query. |
| run | [uint32](#uint32) | optional | Run number to query. |
| instance | [uint32](#uint32) | optional | Instance to query. |
| dataparams | [gsi.pb.DataParam.Type](#gsi-pb-DataParam-Type) | repeated | List of DataParams to query. |
| points | [gsi.pb.Point](#gsi-pb-Point) | repeated | List of spatial coordinates to query at. |






<a name="gsi-pb-project-QueryResultsAtCoordinatesResponse"></a>

### QueryResultsAtCoordinatesResponse
Response message for QueryResultsAtCoordinates.
Map of uint32 and ParamResults, representing a mapping between a DataParam and the results data for that DataParam.


| Field | Type | Label | Description |
| ----- | ---- | ----- | ----------- |
| results | [QueryResultsAtCoordinatesResponse.ResultsEntry](#gsi-pb-project-QueryResultsAtCoordinatesResponse-ResultsEntry) | repeated | Map of DataParam to result data. |






<a name="gsi-pb-project-QueryResultsAtCoordinatesResponse-ResultsEntry"></a>

### QueryResultsAtCoordinatesResponse.ResultsEntry



| Field | Type | Label | Description |
| ----- | ---- | ----- | ----------- |
| key | [uint32](#uint32) |  |  |
| value | [ParamResults](#gsi-pb-project-ParamResults) |  |  |






<a name="gsi-pb-project-QueryResultsAvailabilityRequest"></a>

### QueryResultsAvailabilityRequest
Request message for checking result availability.
Determines whether computed results exist for the specified analysis.


| Field | Type | Label | Description |
| ----- | ---- | ----- | ----------- |
| analysis | [string](#string) | optional | Name of the queried analysis |






<a name="gsi-pb-project-QueryResultsAvailabilityResponse"></a>

### QueryResultsAvailabilityResponse
Response message for QueryResultsAvailability.
Returns a boolean indicating whether results are available for the analysis


| Field | Type | Label | Description |
| ----- | ---- | ----- | ----------- |
| has_results | [bool](#bool) | optional | True if results are available, false otherwise |






<a name="gsi-pb-project-QueryResultsRequest"></a>

### QueryResultsRequest
Request message for QueryResults.
Takes an analysis name, a list of slips, the DataTable, and the list of DataParams to query


| Field | Type | Label | Description |
| ----- | ---- | ----- | ----------- |
| analysis | [string](#string) | optional | Name of the queried analysis |
| step | [uint32](#uint32) | optional | Step number to query |
| run | [uint32](#uint32) | optional | Run number to query |
| instance | [uint32](#uint32) | optional | Instance to query |
| table | [gsi.pb.Result.Type](#gsi-pb-Result-Type) | optional | DataTable to query |
| dataparams | [gsi.pb.DataParam.Type](#gsi-pb-DataParam-Type) | repeated | List of DataParams to query |
| result_ids | [uint32](#uint32) | repeated | A list of unique identifiers corresponding to the specific datatable (e.g., node number, Gauss point number, element number, etc) This is used to filter the results returned |






<a name="gsi-pb-project-QueryResultsResponse"></a>

### QueryResultsResponse
Response message for QueryResults.
Returns a map of uint32 and ParamResults, representing a mapping between a DataParam and the results data for that DataParam


| Field | Type | Label | Description |
| ----- | ---- | ----- | ----------- |
| results | [QueryResultsResponse.ResultsEntry](#gsi-pb-project-QueryResultsResponse-ResultsEntry) | repeated | Map of DataParam to result data. |






<a name="gsi-pb-project-QueryResultsResponse-ResultsEntry"></a>

### QueryResultsResponse.ResultsEntry



| Field | Type | Label | Description |
| ----- | ---- | ----- | ----------- |
| key | [uint32](#uint32) |  |  |
| value | [ParamResults](#gsi-pb-project-ParamResults) |  |  |






<a name="gsi-pb-project-QueryTableParamsInfoRequest"></a>

### QueryTableParamsInfoRequest
Request message for QueryTableParamsInfo.
Takes an analysis name and a ResultType.


| Field | Type | Label | Description |
| ----- | ---- | ----- | ----------- |
| analysis | [string](#string) | optional | Name of the queried analysis |
| table | [gsi.pb.Result.Type](#gsi-pb-Result-Type) | optional | Table to query |






<a name="gsi-pb-project-QueryTableParamsInfoResponse"></a>

### QueryTableParamsInfoResponse
Response message for QueryTableParamsInfo.
Returns metadata for all available parameters in the requested table.


| Field | Type | Label | Description |
| ----- | ---- | ----- | ----------- |
| params_info | [ParamInfo](#gsi-pb-project-ParamInfo) | repeated | List of parameter metadata. |






<a name="gsi-pb-project-SetRequest"></a>

### SetRequest
Request message for setting data for a specific analysis and object.


| Field | Type | Label | Description |
| ----- | ---- | ----- | ----------- |
| analysis | [string](#string) | optional | Name of the analysis |
| object | [string](#string) | optional | Name of the object |
| data | [google.protobuf.Value](#google-protobuf-Value) | optional | The data to set. |






<a name="gsi-pb-project-SetResponse"></a>

### SetResponse
Response message for SetRequest.






<a name="gsi-pb-project-SolveAnalysesRequest"></a>

### SolveAnalysesRequest
The request to solve a list of analyses at a given step number. Takes in a list of strings representing analysis names and an integer representing the step number
By default, solve all analyses dependant on the given analyses unless a step number is provided.

Note: When solving multiple analyses or when solve_dependencies is true, the step parameter should not be used.
The step parameter is only valid when solving a single analysis with solve_dependencies set to false.


| Field | Type | Label | Description |
| ----- | ---- | ----- | ----------- |
| analyses | [string](#string) | repeated | A list of the analyses to solve |
| step | [uint32](#uint32) | optional | The step number to solve. |
| solve_dependencies | [bool](#bool) | optional | If true, analyses dependant on the given analyses will also be solved. |






<a name="gsi-pb-project-SolveAnalysesResponse"></a>

### SolveAnalysesResponse
Response message for SolveAnalyses.
Returns a map where each key is an analysis name and the value is the result of attempting to solve that analysis.
Each result contains a success flag and an optional error message if the solve failed.


| Field | Type | Label | Description |
| ----- | ---- | ----- | ----------- |
| completion_status | [SolveAnalysesResponse.CompletionStatusEntry](#gsi-pb-project-SolveAnalysesResponse-CompletionStatusEntry) | repeated | key = analysis name |
| all_succeeded | [bool](#bool) | optional | False if any analysis failed |






<a name="gsi-pb-project-SolveAnalysesResponse-CompletionStatusEntry"></a>

### SolveAnalysesResponse.CompletionStatusEntry



| Field | Type | Label | Description |
| ----- | ---- | ----- | ----------- |
| key | [string](#string) |  |  |
| value | [SolveAnalysesResult](#gsi-pb-project-SolveAnalysesResult) |  |  |






<a name="gsi-pb-project-SolveAnalysesResult"></a>

### SolveAnalysesResult
Inner message type for SolveAnalysesResponse.
Indicates whether the analysis solve was successful and provides an error message if it was not.


| Field | Type | Label | Description |
| ----- | ---- | ----- | ----------- |
| succeeded | [bool](#bool) | optional | True if the solve succeeded. |
| error_message | [string](#string) | optional | Error message if the solve failed. |





 

 

 


<a name="gsi-pb-project-Project"></a>

### Project
Project Interface

| Method Name | Request Type | Response Type | Description |
| ----------- | ------------ | ------------- | ------------|
| Get | [GetRequest](#gsi-pb-project-GetRequest) | [GetResponse](#gsi-pb-project-GetResponse) | Retrieves object data (properties, parameters, geometry) for a specified object in a given analysis. |
| Set | [SetRequest](#gsi-pb-project-SetRequest) | [SetResponse](#gsi-pb-project-SetResponse) | Sets object data (properties, parameters, geometry) for a specified analysis and object. |
| Add | [AddRequest](#gsi-pb-project-AddRequest) | [AddResponse](#gsi-pb-project-AddResponse) | Adds a new object with specified data to a given analysis. |
| Delete | [DeleteRequest](#gsi-pb-project-DeleteRequest) | [DeleteResponse](#gsi-pb-project-DeleteResponse) | Deletes an object from a specified analysis. |
| SolveAnalyses | [SolveAnalysesRequest](#gsi-pb-project-SolveAnalysesRequest) | [SolveAnalysesResponse](#gsi-pb-project-SolveAnalysesResponse) | Solves a list of analyses at a given step number. |
| QueryTableParamsInfo | [QueryTableParamsInfoRequest](#gsi-pb-project-QueryTableParamsInfoRequest) | [QueryTableParamsInfoResponse](#gsi-pb-project-QueryTableParamsInfoResponse) | Query metadata for all DataParams stored in a requested DataTable. |
| QueryResults | [QueryResultsRequest](#gsi-pb-project-QueryResultsRequest) | [QueryResultsResponse](#gsi-pb-project-QueryResultsResponse) | Query results for an analysis. |
| QueryResultsAtCoordinates | [QueryResultsAtCoordinatesRequest](#gsi-pb-project-QueryResultsAtCoordinatesRequest) | [QueryResultsAtCoordinatesResponse](#gsi-pb-project-QueryResultsAtCoordinatesResponse) | Query spatial results for an analysis |
| QueryResultsAvailability | [QueryResultsAvailabilityRequest](#gsi-pb-project-QueryResultsAvailabilityRequest) | [QueryResultsAvailabilityResponse](#gsi-pb-project-QueryResultsAvailabilityResponse) | Query whether an analysis has results available |
| LoadResults | [LoadResultsRequest](#gsi-pb-project-LoadResultsRequest) | [LoadResultsResponse](#gsi-pb-project-LoadResultsResponse) | Loads results for a specified analysis. |

 



## Scalar Value Types

| .proto Type | Notes | C++ | Java | Python | Go | C# | PHP | Ruby |
| ----------- | ----- | --- | ---- | ------ | -- | -- | --- | ---- |
| <a name="double" /> double |  | double | double | float | float64 | double | float | Float |
| <a name="float" /> float |  | float | float | float | float32 | float | float | Float |
| <a name="int32" /> int32 | Uses variable-length encoding. Inefficient for encoding negative numbers – if your field is likely to have negative values, use sint32 instead. | int32 | int | int | int32 | int | integer | Bignum or Fixnum (as required) |
| <a name="int64" /> int64 | Uses variable-length encoding. Inefficient for encoding negative numbers – if your field is likely to have negative values, use sint64 instead. | int64 | long | int/long | int64 | long | integer/string | Bignum |
| <a name="uint32" /> uint32 | Uses variable-length encoding. | uint32 | int | int/long | uint32 | uint | integer | Bignum or Fixnum (as required) |
| <a name="uint64" /> uint64 | Uses variable-length encoding. | uint64 | long | int/long | uint64 | ulong | integer/string | Bignum or Fixnum (as required) |
| <a name="sint32" /> sint32 | Uses variable-length encoding. Signed int value. These more efficiently encode negative numbers than regular int32s. | int32 | int | int | int32 | int | integer | Bignum or Fixnum (as required) |
| <a name="sint64" /> sint64 | Uses variable-length encoding. Signed int value. These more efficiently encode negative numbers than regular int64s. | int64 | long | int/long | int64 | long | integer/string | Bignum |
| <a name="fixed32" /> fixed32 | Always four bytes. More efficient than uint32 if values are often greater than 2^28. | uint32 | int | int | uint32 | uint | integer | Bignum or Fixnum (as required) |
| <a name="fixed64" /> fixed64 | Always eight bytes. More efficient than uint64 if values are often greater than 2^56. | uint64 | long | int/long | uint64 | ulong | integer/string | Bignum |
| <a name="sfixed32" /> sfixed32 | Always four bytes. | int32 | int | int | int32 | int | integer | Bignum or Fixnum (as required) |
| <a name="sfixed64" /> sfixed64 | Always eight bytes. | int64 | long | int/long | int64 | long | integer/string | Bignum |
| <a name="bool" /> bool |  | bool | boolean | boolean | bool | bool | boolean | TrueClass/FalseClass |
| <a name="string" /> string | A string must always contain UTF-8 encoded or 7-bit ASCII text. | string | String | str/unicode | string | string | string | String (UTF-8) |
| <a name="bytes" /> bytes | May contain any arbitrary sequence of bytes. | string | ByteString | str | []byte | ByteString | string | String (ASCII-8BIT) |


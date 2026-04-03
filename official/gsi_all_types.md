# Protocol Documentation
<a name="top"></a>

## Table of Contents

- [gsi_Point.proto](#gsi_Point-proto)
    - [Point](#gsi-pb-Point)
  
- [gsi_Result_Type.proto](#gsi_Result_Type-proto)
    - [Type](#gsi-pb-Result-Type)
  
- [gsi_UnitCategory_Type.proto](#gsi_UnitCategory_Type-proto)
    - [Type](#gsi-pb-UnitCategory-Type)
  
- [Scalar Value Types](#scalar-value-types)



<a name="gsi_Point-proto"></a>
<p align="right"><a href="#top">Top</a></p>

## gsi_Point.proto



<a name="gsi-pb-Point"></a>

### Point
Generic data type representing a 2D or 3D point


| Field | Type | Label | Description |
| ----- | ---- | ----- | ----------- |
| x | [double](#double) | optional |  |
| y | [double](#double) | optional |  |
| z | [double](#double) | optional |  |





 

 

 

 



<a name="gsi_Result_Type-proto"></a>
<p align="right"><a href="#top">Top</a></p>

## gsi_Result_Type.proto


 


<a name="gsi-pb-Result-Type"></a>

### Type
Enumeration indicating the DataTable to be queried

| Name | Number | Description |
| ---- | ------ | ----------- |
| Undefined | 0 |  |
| Nodes | 1000 | Mesh nodes |
| Elements | 1002 | Common mesh elements (1D and 2D combined) |
| Gauss | 1003 | Gauss regions (2D) and points (1D) |
| ElementNodes | 1004 | Element nodes |
| Time | 1005 | An analysis time table |
| NodePair | 1006 | NodePair |
| History | 1009 | A history node table |
| TimeStep | 1010 | An analysis time table |
| FlowPath | 1011 | Flow paths |
| Slip | 1013 | Slope slip surface data |
| CriticalSlip | 1014 | Slope critical slip surface data |
| Column | 1015 | Slope slip surface column data |
| Intercolumn | 1016 | Slope slip surface intercolumn data |
| LambdaFOS | 1017 | Slope slip surface lambda FOS data |
| Reinforcement | 1018 | Slope reinforcement line data on current slip surface |
| Sample | 1023 | Table of sample values used in the current solver run |
| Probabilistic | 1024 | Table of probabilistic results used for histogram generation |
| Iteration | 1025 | Table of solver iteration convergence data |
| BeamGauss | 1026 | Gauss points on a beam element. |
| ParticleAir | 1027 | Air particle history |
| ParticleWater | 1028 | Water particle history |
| SavedTimeStep | 1106 | Saved timestep table |


 

 

 



<a name="gsi_UnitCategory_Type-proto"></a>
<p align="right"><a href="#top">Top</a></p>

## gsi_UnitCategory_Type.proto


 


<a name="gsi-pb-UnitCategory-Type"></a>

### Type
Enumeration indicating the category that the units of a DataParam belongs to

| Name | Number | Description |
| ---- | ------ | ----------- |
| Undefined | 0 |  |
| Length | 1 | Length (fundamental unit type) |
| Time | 2 | Time (fundamental unit type) |
| Force | 3 | Force (fundamental unit type) |
| Temperature | 4 | Temperature (fundamental unit type) |
| Mass | 5 | Mass (fundamental unit type) |
| Energy | 6 | Energy (heat) (fundamental unit type) |
| Angle | 7 | Angle (fundamental unit type) |
| HydraulicHead | 8 | Hydraulic Head (same as Length) |
| MassAir | 9 | Air Mass (same as Mass) |
| MassWater | 10 | Water Mass (same as Mass) |
| MassSolute | 11 | Solute Mass (same as Mass) |
| MassGas | 12 | Gas Mass (same as Mass) |
| ForcePerLength | 13 | Line Load (Force / Length) |
| SpringConstant | 14 | Spring Constant (same as ForcePerLength) |
| FactoredPullout | 15 | Reinforcement unit (Force / Length/ Length) (Different than Length^2, one length is out of plane). |
| Pressure | 16 | Pressure (Force / Length\x00B2) |
| Strength | 17 | Strength (same as Pressure) |
| Stiffness | 18 | Stiffness (same as Pressure) |
| UnitWeight | 19 | Unit Weight (Force / Length\x00B3) |
| Velocity | 20 | Velocity - Length / Time |
| ClimateVolumeFlux | 21 | Climate Volume Flux - (same as VolumeFluxWater) |
| FluidConductivity | 22 | Fluid Conductivity (same as Velocity) |
| Acceleration | 23 | Acceleration - Length / Time\x00B2 |
| DispersionCoefficient | 24 | Area Velocity - Length\x00B2 / Time |
| DiffusionCoefficient | 25 | Area Velocity - Length\x00B2 / Time |
| VolumeRateWater | 26 | Water Volume Velocity (same as VolumeRate) |
| VolumeRateAir | 27 | Air Volume Velocity (same as VolumeRate) |
| Density | 28 | Density - Mass / LENGTH\x00B3 |
| Concentration | 29 | Concentration - same as Density - Mass / LENGTH\x00B3 |
| MassRate | 30 | Mass Flux - Mass / Time |
| MassRateAir | 31 | Air Mass Flux (same as MassRate) |
| MassRateWater | 32 | Water Mass Flux (same as MassRate) |
| MassRateSolute | 33 | Solute Mass Flux (same as MassRate) |
| MassRateGas | 34 | Gas Mass Flux (same as MassRate) |
| EnergyRate | 35 | Energy (heat) Rate - Energy / Time |
| VolumetricSpecificHeat | 36 | Volumetric Specific Heat - Energy / Length\x00B3 / Temp |
| Moment | 37 | Moment - Force * Length |
| Compressibility | 38 | Amount per units of pressure - 1 / Pressure (e.g., for Beta) |
| PerDensity | 39 | Amount per units of density - 1 / Density or LENGTH\x00B3 / Mass (e.g., for AdsorptionSlope) |
| Frequency | 40 | Frequency (Rate per time) - 1 / Time |
| ReactionRate | 41 | Reaction Rate = 1 / Time |
| ThermalConductivity | 42 | Energy (Thermal) Conductivity - Energy / Time / Length / Temp |
| Area | 43 | Area - Length\x00B2 |
| Volume | 44 | Volume - Length\x00B3 |
| VolumeWater | 45 | Water Volume (same as Volume) |
| VolumeAir | 46 | Air Volume (same as Volume) |
| UnitMassFlux | 52 | Mass unit flux (qm) - Mass / Time / Length |
| LatentHeat | 53 | Energy per volume - Energy/Length\x00B3 |
| MomentOfInertia | 54 | Moment of Inertia - Length^4 (Note: this is not the same as Moment) |
| GradientEnergy | 55 | Energy (heat) gradient (Temp / Length) |
| SpecificHeat | 57 | Energy / Mass / Temp - Energy / Mass / Temp |
| MassPerMass | 62 | Mass per mass	(Mass/Mass) |
| StrengthPerDepth | 63 | Pressure per unit of length	(Pressure / Length) |
| ConvectiveHeatTransferCoefficient | 64 | Convective Heat Transfer Coefficient - Energy / Time / Area / Temp |
| VolumeFluxWater | 65 | Water Volume Flux - Volume/Time/Area |
| VolumeFluxAir | 66 | Air Volume Flux - Volume/Time/Area |
| EnergyFlux | 67 | Energy Flux - Energy/Time/Area |
| MassFlux | 68 | Mass Flux - Mass/Time/Area |
| MassFluxAir | 69 | Air Mass Flux (same as MassFlux) |
| MassFluxWater | 70 | Water Mass Flux (same as MassFlux) |
| MassFluxSolute | 71 | Solute Mass Flux (same as MassFlux) |
| MassFluxGas | 72 | Gas Mass Flux (same as MassFlux) |
| SnowDepth | 73 | Snow Depth (same as Length) |
| RootDepth | 74 | Root Depth (same as Length) |
| VegetationHeight | 75 | Vegetation Height (same as Length) |
| Displacement | 77 | Sigma/Quake Displacement (same as Length) |
| DeltaTemperature | 78 | Incremental temperature value (normalized temperature) |


 

 

 



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


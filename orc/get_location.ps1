Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]

function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(4000) | Out-Null
    return $netTask.Result
}

try {
    [Windows.Devices.Geolocation.Geolocator, Windows.Devices.Geolocation, ContentType = WindowsRuntime] | Out-Null
    $geolocator = New-Object Windows.Devices.Geolocation.Geolocator
    $posOp = $geolocator.GetGeopositionAsync()
    $pos = Await $posOp ([Windows.Devices.Geolocation.Geoposition])
    if ($pos) {
        $lat = $pos.Coordinate.Point.Position.Latitude
        $lon = $pos.Coordinate.Point.Position.Longitude
        $acc = $pos.Coordinate.Accuracy
        Write-Output "$lat,$lon,$acc"
    }
} catch {
}

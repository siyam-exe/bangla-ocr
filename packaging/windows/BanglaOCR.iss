#ifndef StageDir
  #error StageDir must point to the prepared Bangla OCR directory
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef RuntimeName
  #define RuntimeName "cpu"
#endif
#ifndef HardwareProbePath
  #error HardwareProbePath must point to the compiled hardware probe
#endif
#ifndef AppIconPath
  #error AppIconPath must point to the Bangla OCR icon
#endif
#ifndef UninstallableValue
  #define UninstallableValue "yes"
#endif
#ifndef CpuRuntimeSize
  #define CpuRuntimeSize "size unavailable"
#endif
#ifndef CudaRuntimeSize
  #define CudaRuntimeSize "size unavailable"
#endif
#ifndef VulkanRuntimeSize
  #define VulkanRuntimeSize "size unavailable"
#endif

[Setup]
AppId={{49CF939D-9704-4518-A22C-E905D2711595}
AppName=Bangla OCR
AppVersion={#AppVersion}
AppVerName=Bangla OCR {#AppVersion}
AppPublisher=Siyam
AppPublisherURL=https://github.com/siyam-exe/bangla-ocr
AppSupportURL=https://github.com/siyam-exe/bangla-ocr/issues
DefaultDirName={localappdata}\Programs\Bangla OCR
DisableDirPage=no
UsePreviousAppDir=yes
DefaultGroupName=Bangla OCR
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=Bangla-OCR-{#AppVersion}-windows-x64-{#RuntimeName}-setup
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes
SetupIconFile={#AppIconPath}
Uninstallable={#UninstallableValue}
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\Bangla OCR.exe
UninstallDisplayName=Bangla OCR {#AppVersion}
VersionInfoVersion={#AppVersion}
VersionInfoCompany=Siyam
VersionInfoDescription=Bangla OCR universal Windows installer
VersionInfoProductName=Bangla OCR

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Excludes: "\tools\llama.cpp-cpu\*,\tools\llama.cpp-cuda\*,\tools\llama.cpp-vulkan\*"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageDir}\tools\llama.cpp-cpu\*"; DestDir: "{app}\tools\llama.cpp-cpu"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist solidbreak; Check: InstallCpuRuntime
Source: "{#StageDir}\tools\llama.cpp-cuda\*"; DestDir: "{app}\tools\llama.cpp-cuda"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist solidbreak; Check: InstallCudaRuntime
Source: "{#StageDir}\tools\llama.cpp-vulkan\*"; DestDir: "{app}\tools\llama.cpp-vulkan"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist solidbreak; Check: InstallVulkanRuntime
Source: "{#HardwareProbePath}"; Flags: dontcopy

[InstallDelete]
Type: filesandordirs; Name: "{app}\runtime\logs"
Type: filesandordirs; Name: "{app}\runtime\surya"
Type: filesandordirs; Name: "{app}\runtime\temp"
Type: filesandordirs; Name: "{app}\tools\llama.cpp-cpu"
Type: filesandordirs; Name: "{app}\tools\llama.cpp-cuda"
Type: filesandordirs; Name: "{app}\tools\llama.cpp-vulkan"

[Dirs]
Name: "{app}\documents"
Name: "{app}\documents\imports"
Name: "{app}\models"
Name: "{app}\workspace\logs"
Name: "{app}\workspace\surya"
Name: "{app}\workspace\temp"

[Icons]
Name: "{group}\Bangla OCR"; Filename: "{app}\Bangla OCR.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Bangla OCR"; Filename: "{app}\Bangla OCR.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Bangla OCR.exe"; Description: "Open Bangla OCR"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\workspace"

[Code]
var
  RuntimePage: TInputOptionWizardPage;
  HardwareRecommendation: String;
  HardwareAdapters: String;
  HardwareReason: String;

procedure DetectHardware;
var
  I: Integer;
  Separator: Integer;
  ResultCode: Integer;
  Key: String;
  Value: String;
  ProbeOutput: TExecOutput;
begin
  HardwareRecommendation := 'cpu';
  HardwareAdapters := 'No supported graphics adapter reported';
  HardwareReason := 'Hardware detection could not finish. CPU OCR will be installed and may be slower.';
  ExtractTemporaryFile(ExtractFileName('{#HardwareProbePath}'));
  if not ExecAndCaptureOutput(
    ExpandConstant('{tmp}\') + ExtractFileName('{#HardwareProbePath}'),
    '--installer', '', SW_HIDE, ewWaitUntilTerminated, ResultCode, ProbeOutput) then
  begin
    Log('The hardware probe could not be started. Using the CPU recommendation.');
    exit;
  end;
  if (ResultCode <> 0) or ProbeOutput.Error then
  begin
    Log('The hardware probe failed. Using the CPU recommendation.');
    exit;
  end;

  for I := 0 to GetArrayLength(ProbeOutput.StdOut) - 1 do
  begin
    Separator := Pos('=', ProbeOutput.StdOut[I]);
    if Separator > 0 then
    begin
      Key := Lowercase(Trim(Copy(ProbeOutput.StdOut[I], 1, Separator - 1)));
      Value := Trim(Copy(ProbeOutput.StdOut[I], Separator + 1, MaxInt));
      if Key = 'recommendation' then
        HardwareRecommendation := Lowercase(Value)
      else if (Key = 'adapters') and (Value <> '') then
        HardwareAdapters := Value
      else if (Key = 'reason') and (Value <> '') then
        HardwareReason := Value;
    end;
  end;
end;

procedure SelectRuntimes;
var
  Requested: String;
begin
  RuntimePage.Values[0] := True;
  RuntimePage.Values[1] := False;
  RuntimePage.Values[2] := False;

  Requested := Lowercase(Trim(ExpandConstant('{param:RUNTIME|auto}')));
  if Requested = 'auto' then
    Requested := HardwareRecommendation;

  if Requested = 'cuda' then
    RuntimePage.Values[1] := True
  else if Requested = 'vulkan' then
    RuntimePage.Values[2] := True
  else if Requested = 'all' then
  begin
    RuntimePage.Values[1] := True;
    RuntimePage.Values[2] := True;
  end
  else if Requested <> 'cpu' then
    Log('Unknown /RUNTIME value. Using CPU only: ' + Requested);
end;

procedure InitializeWizard;
begin
  DetectHardware;
  RuntimePage := CreateInputOptionPage(
    wpSelectDir,
    'OCR acceleration',
    'Choose what Bangla OCR should install',
    'Detected graphics: ' + HardwareAdapters + #13#10 + #13#10 + HardwareReason + #13#10 +
      'The recommended choices are already selected. Change them only if you know which runtime your computer supports.',
    False,
    False
  );
  RuntimePage.Add('CPU OCR fallback (required, slower) - {#CpuRuntimeSize}');
  RuntimePage.Add('NVIDIA CUDA acceleration - {#CudaRuntimeSize}');
  RuntimePage.Add('Vulkan acceleration for AMD or Intel graphics (experimental) - {#VulkanRuntimeSize}');
  SelectRuntimes;
  RuntimePage.CheckListBox.ItemEnabled[0] := False;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  if CurPageID = RuntimePage.ID then
    RuntimePage.Values[0] := True;
  Result := True;
end;

function InstallCpuRuntime: Boolean;
begin
  Result := True;
end;

function InstallCudaRuntime: Boolean;
begin
  Result := RuntimePage.Values[1];
end;

function InstallVulkanRuntime: Boolean;
begin
  Result := RuntimePage.Values[2];
end;

function RuntimeSummary: String;
begin
  Result := 'CPU fallback';
  if RuntimePage.Values[1] then
    Result := Result + ', NVIDIA CUDA';
  if RuntimePage.Values[2] then
    Result := Result + ', Vulkan (experimental)';
end;

function UpdateReadyMemo(
  Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo,
  MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := MemoDirInfo + NewLine + NewLine +
    'OCR runtimes:' + NewLine + Space + RuntimeSummary;
  if MemoGroupInfo <> '' then
    Result := Result + NewLine + NewLine + MemoGroupInfo;
  if MemoTasksInfo <> '' then
    Result := Result + NewLine + NewLine + MemoTasksInfo;
end;

[Code]
function GetFileAttributes(Name: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';
function OpenOwnedFile(Name: String; Access, Share: LongWord;
  Security: Integer; Creation, Flags: LongWord; Template: Integer): Integer;
  external 'CreateFileW@kernel32.dll stdcall';
function CloseOwnedFile(Handle: Integer): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

function CheckOwnedTree(Name: String): Boolean;
var
  Item: TFindRec;
  Handle: Integer;
begin
  Result := False;
  { Never follow junctions or symbolic links during replacement. }
  if (GetFileAttributes(Name) and $400) <> 0 then Exit;
  if DirExists(Name) then begin
    Result := True;
    if FindFirst(Name + '\*', Item) then begin
      try
        repeat
          if (Item.Name <> '.') and (Item.Name <> '..') then
            if not CheckOwnedTree(Name + '\' + Item.Name) then begin
              Result := False;
              Break;
            end;
        until not FindNext(Item);
      finally
        FindClose(Item);
      end;
    end;
  end else begin
    { Require exclusive write access; do not terminate measurement processes. }
    Handle := OpenOwnedFile(Name, $C0000000, 0, 0, 3, 0, 0);
    if Handle <> -1 then begin
      CloseOwnedFile(Handle);
      Result := True;
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Previous, App, Resources: String;
begin
  Result := '';
  App := ExpandConstant('{app}');
  Resources := App + '\resources';
  if DirExists(App) and ((GetFileAttributes(App) and $400) <> 0) then begin
    Result := 'Scopecat cannot install through a directory link.';
    Exit;
  end;
  if not DirExists(Resources) then Exit;
  if not RegQueryStringValue(HKCU,
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\org.scopecat.desktop_is1',
    'InstallLocation', Previous) or
    (CompareText(RemoveBackslashUnlessRoot(Previous), RemoveBackslashUnlessRoot(App)) <> 0) then begin
    Result := 'This resources directory is not a registered Scopecat installation. Choose an empty directory.';
    Exit;
  end;
  if not CheckOwnedTree(Resources) or
    (FileExists(App + '\Scopecat.exe') and not CheckOwnedTree(App + '\Scopecat.exe')) then
    Result := 'Close Scopecat and its Python processes before retrying. Application files must be writable and must not contain directory links.';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then begin
    { Replace the entire owned runtime, including obsolete wheels and bytecode.
      User workspaces and execution environments live outside this directory. }
    if DirExists(ExpandConstant('{app}\resources')) then
      if not DelTree(ExpandConstant('{app}\resources'), True, True, True) then
        RaiseException('Could not remove the previous Scopecat runtime. Close its processes and reinstall.');
  end;
end;

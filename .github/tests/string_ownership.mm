#include "4DPluginAPI.h"
#include "PrivateTypes.h"
#include "EntryPoints.h"

#include <cassert>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <set>

extern Call4DProcPtr gCall4D;

static std::set<PA_Unichar*> strings;
static std::map<PA_Handle, size_t> handles;
static unsigned doubleDisposals = 0;
static unsigned commands = 0;
static bool commandFails = false;
static bool failNextHandle = false;
static CUTF16String expectedFormat;
static const PA_Unichar path[] = {'/', 't', 'm', 'p', '/', 'p', '.', 'p', 'n', 'g', 0};

static PA_Unistring makeString(const PA_Unichar* text)
{
    size_t length = 0;
    while (text[length])
        ++length;
    PA_Unichar* buffer = new PA_Unichar[length + 1];
    std::memcpy(buffer, text, (length + 1) * sizeof(PA_Unichar));
    strings.insert(buffer);
    PA_Unistring value = {};
    value.fString = buffer;
    value.fLength = static_cast<PA_long32>(length);
    return value;
}

static void disposeString(PA_Unistring value)
{
    PA_Unichar* buffer = value.fString;
    if (!buffer)
        return;
    if (!strings.erase(buffer))
    {
        ++doubleDisposals;
        return;
    }
    delete[] buffer;
}

static CUTF16String readString(PA_Unistring value)
{
    PA_Unichar* buffer = value.fString;
    assert(buffer && strings.count(buffer));
    return CUTF16String(buffer, value.fLength);
}

static void clearVariable(PA_Variable* variable)
{
    if (variable->fType == eVK_Unistring)
        disposeString(variable->uValue.fString);
    else if (variable->fType == eVK_ArrayUnicode)
    {
        PA_Unistring* data = reinterpret_cast<PA_Unistring*>(*variable->uValue.fArray.fData);
        for (PA_long32 i = 0; i <= variable->uValue.fArray.fNbElements; ++i)
            disposeString(data[i]);
        PA_DisposeHandle(variable->uValue.fArray.fData);
    }
    *variable = PA_CreateVariable(eVK_Undefined);
}

static void host(short selector, EngineBlock* eb)
{
    eb->fError = 0;
    switch (selector)
    {
        case EX_CREATE_UNISTRING:
            eb->fUniString1 = makeString(reinterpret_cast<PA_Unichar*>(eb->fHandle));
            break;
        case EX_DISPOSE_UNISTRING:
            disposeString(eb->fUniString1);
            break;
        case EX_CLEAR_VARIABLE:
            clearVariable(static_cast<PA_Variable*>(eb->fPtr1));
            break;
        case EX_HANDLE_MANAGER:
        {
            PA_Handle handle = eb->fParam1 == 1 ? nullptr : eb->fHandle;
            switch (eb->fParam1)
            {
                case 1:
                    if (failNextHandle)
                    {
                        failNextHandle = false;
                        eb->fHandle = nullptr;
                        eb->fError = eER_IncompatibleTypes;
                        break;
                    }
                    eb->fHandle = new char*;
                    *eb->fHandle = static_cast<char*>(std::calloc(1, eb->fParam2));
                    handle = eb->fHandle;
                    handles[handle] = eb->fParam2;
                    break;
                case 2:
                    assert(handles.erase(handle) == 1);
                    std::free(*eb->fHandle);
                    delete eb->fHandle;
                    break;
                case 3:
                {
                    char* data = static_cast<char*>(std::realloc(*eb->fHandle, eb->fParam2));
                    assert(data);
                    *eb->fHandle = data;
                    handles.at(handle) = eb->fParam2;
                    break;
                }
                case 4:
                    eb->fParam2 = handles.at(handle);
                    break;
                case 5:
                    eb->fParam3 = reinterpret_cast<sLONG_PTR>(*eb->fHandle);
                    break;
                case 6:
                    break;
                default:
                    std::abort();
            }
            break;
        }
        case EX_CREATE_PICTURE:
            // Picture pixels/refcounts are outside this string-lifetime model.
            eb->fPicture = nullptr;
            break;
        case EX_DISPOSE_PICTURE:
        case EX_DUPLICATE_PICTURE:
            break;
        case EX_CONVERT_CHARSET_TO_CHARSET:
        {
            assert(eb->fParam2 == eVTC_UTF_8 && eb->fParam4 == eVTC_UTF_16);
            CFStringRef text = CFStringCreateWithBytes(
                kCFAllocatorDefault, static_cast<const UInt8*>(eb->fPtr1),
                eb->fParam1, kCFStringEncodingUTF8, false);
            assert(text);
            CFIndex length = CFStringGetLength(text);
            assert(eb->fParam3 >= length * sizeof(PA_Unichar));
            PA_Unichar* output = static_cast<PA_Unichar*>(eb->fPtr2);
            CFStringGetCharacters(text, CFRangeMake(0, length), output);
            expectedFormat.assign(output, length);
            eb->fParam3 = length * sizeof(PA_Unichar);
            assert(eb->fParam3 % sizeof(PA_Unichar) == 0);
            CFRelease(text);
            break;
        }
        case EX_EXECUTE_COMMAND_BY_ID:
        {
            PA_Variable** args = static_cast<PA_Variable**>(eb->fPtr1);
            assert(eb->fParam2 == 2);
            PA_Variable* argument = args[eb->fParam1 == 678 ? 0 : 1];
            assert(argument->fType == eVK_Unistring);
            PA_Unistring text = argument->uValue.fString;
            CUTF16String before = readString(text);
            if (eb->fParam1 == 678)
                assert(before == CUTF16String(path));
            else
            {
                assert(eb->fParam1 == 1002);
                assert(before == expectedFormat);
            }
            PA_Unistring independent = makeString(path);
            PA_DisposeUnistring(&independent);
            assert(readString(text) == before);
            ++commands;
            if (commandFails)
                eb->fError = eER_IncompatibleTypes;
            break;
        }
        default:
            std::cerr << "Unexpected host selector: " << selector << '\n';
            std::abort();
    }
}

void PluginMain(PA_long32, PA_PluginParameters) {}
void CommandDispatcher(PA_long32, sLONG_PTR*, PackagePtr) {}

static void pictureHelpers()
{
    CUTF8String filename(reinterpret_cast<const uint8_t*>("/tmp/p.png"));
    for (unsigned i = 0; i < 20; ++i)
    {
        commandFails = i % 2;
        CPicture picture;
        picture.fromFileAtPath(&filename);
        PA_DisposePicture(picture.createCopy(".png"));
        PA_DisposePicture(picture.createCopy(".jpg"));
        PA_DisposePicture(picture.createCopy(".tif"));
        PA_DisposePicture(picture.createCopy(".\xc3\xa9\xf0\x9f\x98\x80"));
    }
    assert(commands == 100);
    CPicture picture;
    failNextHandle = true;
    picture.fromFileAtPath(&filename);
    failNextHandle = true;
    PA_DisposePicture(picture.createCopy(".png"));
    assert(commands == 100);
}

static void arrayHelpers()
{
    PA_Variable destination = PA_CreateVariable(eVK_Undefined);
    BytePtr parameters[] = {reinterpret_cast<BytePtr>(&destination)};
    for (unsigned i = 0; i < 20; ++i)
    {
        ARRAY_TEXT output;
        output.appendUTF16String(path);
        output.appendUTF16String(path);
        output.toParamAtIndex(parameters, 1);
        assert(destination.fType == eVK_ArrayUnicode);
        assert(readString(PA_GetStringInArray(destination, 1)) == CUTF16String(path));
        ARRAY_TEXT input;
        input.fromParamAtIndex(parameters, 1);
        PA_ResizeArray(&destination, 0);
        CUTF16String copied;
        input.copyUTF16StringAtIndex(&copied, 1);
        assert(copied == CUTF16String(path));
    }
    PA_ClearVariable(&destination);

    PA_Variable invalid = PA_CreateVariable(eVK_Longint);
    PA_Unistring owned = makeString(path);
    PA_SetStringInArray(invalid, 0, &owned);
    assert(readString(owned) == CUTF16String(path));
    PA_Variable empty = PA_CreateVariable(eVK_ArrayUnicode);
    PA_SetStringInArray(empty, -1, &owned);
    PA_SetStringInArray(empty, 1, &owned);
    assert(readString(owned) == CUTF16String(path));
    PA_DisposeUnistring(&owned);
    PA_ClearVariable(&empty);
}

int main()
{
    gCall4D = host;
    pictureHelpers();
    arrayHelpers();
    std::cerr << "double disposals=" << doubleDisposals
              << ", live strings=" << strings.size()
              << ", live handles=" << handles.size() << '\n';
    const bool passed = !doubleDisposals && strings.empty() && handles.empty();
    for (PA_Unichar* value : strings)
        delete[] value;
    return passed ? 0 : 1;
}

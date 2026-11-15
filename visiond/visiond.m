// One-shot vision helper. It runs, does one thing and exits, so the camera is open only while a picture is being taken.
//   julius-visiond camera OUT.jpg     one camera frame -> OUT.jpg. Prints "ok WxH" or "error <why>".
//   julius-visiond faces              reads one image (JPEG or PNG bytes) on stdin, prints {"w":N,"h":N,"faces":[[x,y,w,h],...]} with each box
//        in 0..1 of the image, origin top-left, largest first. Apple Vision, on this Mac only; nothing is written anywhere.
//   julius-visiond live SECONDS       camera on for SECONDS with a mirrored preview: prints "frame <base64 jpeg>" lines (about 5 a second),
//        then "photo <base64 jpeg>" (one full 1280x720 frame, not mirrored), then "ok WxH"; the session stops so the indicator goes off.
//        Nothing touches the disk: the picture leaves only through stdout.
//   julius-visiond status [owner,owner,...]
//        prints one JSON line: {"locked":bool,"secure_input":bool,"window":{"id":N,"owner":"..","title":"..","w":N,"h":N}|null}
//        window = the frontmost normal on-screen window whose owner is not in the exclude list (Julius's own orb and panel).
//        secure_input is true while any password field has keyboard focus (macOS secure event input).
#import <AVFoundation/AVFoundation.h>
#import <CoreImage/CoreImage.h>
#import <Carbon/Carbon.h>
#import <Vision/Vision.h>
#import <Foundation/Foundation.h>

@interface Grab : NSObject <AVCaptureVideoDataOutputSampleBufferDelegate>
@property int frames;
@property (strong) CIImage *image;
@property (strong) dispatch_semaphore_t done;
@end
@implementation Grab
- (void)captureOutput:(AVCaptureOutput *)o didOutputSampleBuffer:(CMSampleBufferRef)sb fromConnection:(AVCaptureConnection *)c {
    self.frames++;
    if (self.frames < 12) return;  // let auto-exposure and white balance settle (~0.4 s)
    if (self.image) return;
    CVImageBufferRef buf = CMSampleBufferGetImageBuffer(sb);
    if (buf) { self.image = [CIImage imageWithCVImageBuffer:buf]; dispatch_semaphore_signal(self.done); }
}
@end

static int camera(const char *out) {
    AVAuthorizationStatus st = [AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeVideo];
    if (st == AVAuthorizationStatusNotDetermined) {
        dispatch_semaphore_t s = dispatch_semaphore_create(0);
        [AVCaptureDevice requestAccessForMediaType:AVMediaTypeVideo completionHandler:^(BOOL ok) { dispatch_semaphore_signal(s); }];
        dispatch_semaphore_wait(s, dispatch_time(DISPATCH_TIME_NOW, 60 * NSEC_PER_SEC));
        st = [AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeVideo];
    }
    if (st != AVAuthorizationStatusAuthorized) { puts("error camera-permission"); return 3; }
    AVCaptureDevice *dev = [AVCaptureDevice defaultDeviceWithMediaType:AVMediaTypeVideo];
    if (!dev) { puts("error no-camera"); return 4; }
    NSError *err = nil;
    AVCaptureDeviceInput *in = [AVCaptureDeviceInput deviceInputWithDevice:dev error:&err];
    if (!in) { puts("error camera-busy"); return 5; }
    AVCaptureSession *session = [[AVCaptureSession alloc] init];
    session.sessionPreset = AVCaptureSessionPreset1280x720;
    AVCaptureVideoDataOutput *vo = [[AVCaptureVideoDataOutput alloc] init];
    Grab *g = [[Grab alloc] init]; g.done = dispatch_semaphore_create(0);
    dispatch_queue_t q = dispatch_queue_create("julius.visiond", DISPATCH_QUEUE_SERIAL);
    [vo setSampleBufferDelegate:g queue:q];
    if (![session canAddInput:in] || ![session canAddOutput:vo]) { puts("error camera-config"); return 6; }
    [session addInput:in]; [session addOutput:vo];
    [session startRunning];
    long timedOut = dispatch_semaphore_wait(g.done, dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_SEC));
    [session stopRunning];  // the indicator goes off here
    if (timedOut || !g.image) { puts("error no-frame"); return 7; }
    CIContext *ctx = [CIContext contextWithOptions:nil];
    CGColorSpaceRef cs = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    NSURL *url = [NSURL fileURLWithPath:[NSString stringWithUTF8String:out]];
    BOOL ok = [ctx writeJPEGRepresentationOfImage:g.image toURL:url colorSpace:cs options:@{(id)kCGImageDestinationLossyCompressionQuality: @0.9} error:&err];
    CGColorSpaceRelease(cs);
    if (!ok) { puts("error write"); return 8; }
    printf("ok %dx%d\n", (int)g.image.extent.size.width, (int)g.image.extent.size.height);
    return 0;
}

@interface Live : NSObject <AVCaptureVideoDataOutputSampleBufferDelegate>
@property (strong) CIContext *ctx;
@property (strong) dispatch_semaphore_t done;
@property double until;
@property double lastFrame;
@property int frames;
@property BOOL finished;
@property int w, h;
@end
@implementation Live
static NSData *jpeg(CIContext *ctx, CIImage *img, double q) {
    CGColorSpaceRef cs = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    NSData *d = [ctx JPEGRepresentationOfImage:img colorSpace:cs options:@{(id)kCGImageDestinationLossyCompressionQuality: @(q)}];
    CGColorSpaceRelease(cs);
    return d;
}
- (void)captureOutput:(AVCaptureOutput *)o didOutputSampleBuffer:(CMSampleBufferRef)sb fromConnection:(AVCaptureConnection *)c {
    if (self.finished) return;
    self.frames++;
    CVImageBufferRef buf = CMSampleBufferGetImageBuffer(sb);
    if (!buf) return;
    double now = CFAbsoluteTimeGetCurrent();
    CIImage *img = [CIImage imageWithCVImageBuffer:buf];
    if (now >= self.until && self.frames >= 12) {
        NSData *d = jpeg(self.ctx, img, 0.9);
        if (!d) return;
        self.finished = YES;
        self.w = (int)img.extent.size.width; self.h = (int)img.extent.size.height;
        printf("photo %s\n", [[d base64EncodedStringWithOptions:0] UTF8String]); fflush(stdout);
        dispatch_semaphore_signal(self.done);
        return;
    }
    if (now - self.lastFrame < 0.2) return;
    self.lastFrame = now;
    double k = 480.0 / img.extent.size.width;
    CIImage *small = [img imageByApplyingTransform:CGAffineTransformMakeScale(k, k)];
    small = [small imageByApplyingTransform:CGAffineTransformConcat(CGAffineTransformMakeScale(-1, 1), CGAffineTransformMakeTranslation(small.extent.size.width, 0))];
    NSData *d = jpeg(self.ctx, small, 0.6);
    if (d) { printf("frame %s\n", [[d base64EncodedStringWithOptions:0] UTF8String]); fflush(stdout); }
}
@end

static int live(double seconds) {
    AVAuthorizationStatus st = [AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeVideo];
    if (st == AVAuthorizationStatusNotDetermined) {
        dispatch_semaphore_t s = dispatch_semaphore_create(0);
        [AVCaptureDevice requestAccessForMediaType:AVMediaTypeVideo completionHandler:^(BOOL ok) { dispatch_semaphore_signal(s); }];
        dispatch_semaphore_wait(s, dispatch_time(DISPATCH_TIME_NOW, 60 * NSEC_PER_SEC));
        st = [AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeVideo];
    }
    if (st != AVAuthorizationStatusAuthorized) { puts("error camera-permission"); return 3; }
    AVCaptureDevice *dev = [AVCaptureDevice defaultDeviceWithMediaType:AVMediaTypeVideo];
    if (!dev) { puts("error no-camera"); return 4; }
    NSError *err = nil;
    AVCaptureDeviceInput *in = [AVCaptureDeviceInput deviceInputWithDevice:dev error:&err];
    if (!in) { puts("error camera-busy"); return 5; }
    AVCaptureSession *session = [[AVCaptureSession alloc] init];
    session.sessionPreset = AVCaptureSessionPreset1280x720;
    AVCaptureVideoDataOutput *vo = [[AVCaptureVideoDataOutput alloc] init];
    Live *g = [[Live alloc] init];
    g.ctx = [CIContext contextWithOptions:nil]; g.done = dispatch_semaphore_create(0);
    dispatch_queue_t q = dispatch_queue_create("julius.visiond.live", DISPATCH_QUEUE_SERIAL);
    [vo setSampleBufferDelegate:g queue:q];
    if (![session canAddInput:in] || ![session canAddOutput:vo]) { puts("error camera-config"); return 6; }
    [session addInput:in]; [session addOutput:vo];
    g.until = CFAbsoluteTimeGetCurrent() + seconds;
    [session startRunning];
    long timedOut = dispatch_semaphore_wait(g.done, dispatch_time(DISPATCH_TIME_NOW, (int64_t)((seconds + 10) * NSEC_PER_SEC)));
    [session stopRunning];  // the indicator goes off here
    if (timedOut || !g.finished) { puts("error no-frame"); return 7; }
    printf("ok %dx%d\n", g.w, g.h);
    return 0;
}

static int faces(void) {
    NSData *data = [[NSFileHandle fileHandleWithStandardInput] readDataToEndOfFile];
    if (!data.length) { puts("error no-image"); return 3; }
    CIImage *ci = [CIImage imageWithData:data];
    if (!ci) { puts("error bad-image"); return 4; }
    VNDetectFaceRectanglesRequest *req = [[VNDetectFaceRectanglesRequest alloc] init];
    VNImageRequestHandler *h = [[VNImageRequestHandler alloc] initWithData:data options:@{}];
    NSError *err = nil;
    if (![h performRequests:@[req] error:&err]) { puts("error detect"); return 5; }
    NSMutableArray *boxes = [NSMutableArray array];
    for (VNFaceObservation *f in [req.results sortedArrayUsingComparator:^NSComparisonResult(VNFaceObservation *a, VNFaceObservation *b) {
        double A = a.boundingBox.size.width * a.boundingBox.size.height, B = b.boundingBox.size.width * b.boundingBox.size.height;
        return A < B ? NSOrderedDescending : A > B ? NSOrderedAscending : NSOrderedSame; }]) {
        CGRect r = f.boundingBox;  // normalised, origin bottom-left
        [boxes addObject:@[@(r.origin.x), @(1 - r.origin.y - r.size.height), @(r.size.width), @(r.size.height)]];
    }
    NSDictionary *out = @{@"w": @((int)ci.extent.size.width), @"h": @((int)ci.extent.size.height), @"faces": boxes};
    NSData *d = [NSJSONSerialization dataWithJSONObject:out options:0 error:nil];
    printf("%s\n", [[[NSString alloc] initWithData:d encoding:NSUTF8StringEncoding] UTF8String]);
    return 0;
}

static int status(const char *exclude) {
    NSMutableSet *skip = [NSMutableSet set];
    if (exclude && *exclude) for (NSString *n in [[NSString stringWithUTF8String:exclude] componentsSeparatedByString:@","]) [skip addObject:n];
    CFDictionaryRef sess = CGSessionCopyCurrentDictionary();
    BOOL locked = NO;
    if (sess) { locked = [[(__bridge NSDictionary *)sess objectForKey:@"CGSSessionScreenIsLocked"] boolValue]; CFRelease(sess); }
    NSDictionary *win = nil;
    CFArrayRef list = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements, kCGNullWindowID);
    for (NSDictionary *w in (__bridge NSArray *)list) {  // front to back
        if ([w[(id)kCGWindowLayer] intValue] != 0) continue;
        if ([w[(id)kCGWindowAlpha] doubleValue] <= 0.05) continue;
        NSString *owner = w[(id)kCGWindowOwnerName] ?: @"";
        if ([skip containsObject:owner]) continue;
        CGRect r; CGRectMakeWithDictionaryRepresentation((__bridge CFDictionaryRef)w[(id)kCGWindowBounds], &r);
        if (r.size.width < 100 || r.size.height < 100) continue;
        win = @{@"id": w[(id)kCGWindowNumber], @"owner": owner, @"title": w[(id)kCGWindowName] ?: @"", @"w": @((int)r.size.width), @"h": @((int)r.size.height)};
        break;
    }
    if (list) CFRelease(list);
    NSDictionary *out = @{@"locked": @(locked), @"secure_input": @((BOOL)IsSecureEventInputEnabled()), @"window": win ?: [NSNull null]};
    NSData *d = [NSJSONSerialization dataWithJSONObject:out options:0 error:nil];
    printf("%s\n", [[[NSString alloc] initWithData:d encoding:NSUTF8StringEncoding] UTF8String]);
    return 0;
}

int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc >= 3 && !strcmp(argv[1], "camera")) return camera(argv[2]);
        if (argc >= 3 && !strcmp(argv[1], "live")) return live(atof(argv[2]));
        if (argc >= 2 && !strcmp(argv[1], "faces")) return faces();
        if (argc >= 2 && !strcmp(argv[1], "status")) return status(argc >= 3 ? argv[2] : "");
        puts("error usage"); return 2;
    }
}

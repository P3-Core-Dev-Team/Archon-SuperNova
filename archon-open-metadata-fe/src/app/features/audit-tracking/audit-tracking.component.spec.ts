import { ComponentFixture, TestBed } from '@angular/core/testing';

import { AuditTrackingComponent } from './audit-tracking.component';

describe('AuditTrackingComponent', () => {
  let component: AuditTrackingComponent;
  let fixture: ComponentFixture<AuditTrackingComponent>;

  beforeEach(() => {
    TestBed.configureTestingModule({
      declarations: [AuditTrackingComponent]
    });
    fixture = TestBed.createComponent(AuditTrackingComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
